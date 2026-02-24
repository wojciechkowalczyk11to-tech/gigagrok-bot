"""File handlers for multimodal and document analysis."""

from __future__ import annotations

import time

import structlog
from telegram import Message, Update
from telegram.ext import ContextTypes

from config import settings
from db import calculate_cost, save_message, update_daily_stats
from file_utils import (
    detect_file_type,
    extract_text_from_docx,
    extract_text_from_pdf,
    extract_text_from_zip,
    smart_truncate,
)
from grok_client import GrokClient
from handlers.image import analyze_image_bytes
from utils import check_access, escape_html, format_footer, split_message

logger = structlog.get_logger(__name__)

_DEFAULT_FILE_PROMPT = "Przeanalizuj ten plik."


def _text_from_bytes(data: bytes) -> str:
    """Decode plain text file bytes safely."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return data.decode("cp1250")
        except UnicodeDecodeError:
            return data.decode("latin-1", errors="replace")


async def _analyze_text_payload(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    prompt: str,
    payload: str,
    source_label: str,
) -> None:
    """Send extracted file text to Grok with streaming."""
    if not update.effective_user or not update.message:
        return
    user_id = update.effective_user.id

    grok: GrokClient | None = context.application.bot_data.get("grok_client")
    if grok is None:
        await update.message.reply_text("❌ Klient Grok nie został zainicjalizowany.")
        return

    sent = await update.message.reply_text("📎 <i>Analizuję plik...</i>", parse_mode="HTML")
    start_time = time.time()
    full_content = ""
    full_reasoning = ""
    usage: dict[str, int] = {}
    last_edit = 0.0

    content = smart_truncate(payload, max_chars=100_000)
    query = f"{prompt}\n\n=== PLIK ===\n{content}"
    messages = [{"role": "user", "content": query}]

    try:
        async for event_type, data in grok.chat_stream(
            messages=messages,
            model=settings.xai_model_reasoning,
            max_tokens=settings.max_output_tokens,
            reasoning_effort=settings.default_reasoning_effort,
        ):
            if event_type == "reasoning":
                full_reasoning += str(data)
            elif event_type == "content":
                full_content += str(data)
                now = time.time()
                if now - last_edit > 1.5:
                    display = escape_html(full_content[:3800])
                    if len(full_content) > 3800:
                        display += "\n\n<i>... (kontynuacja)</i>"
                    try:
                        await sent.edit_text(display, parse_mode="HTML")
                    except Exception:
                        pass
                    last_edit = now
            elif event_type == "done":
                usage = data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.error("file_analysis_failed", user_id=user_id, error=str(exc))
        await sent.edit_text(f"❌ Błąd API: {escape_html(str(exc))}", parse_mode="HTML")
        return

    elapsed = time.time() - start_time
    tokens_in = usage.get("prompt_tokens", 0)
    tokens_out = usage.get("completion_tokens", 0)
    reasoning_tokens = usage.get("reasoning_tokens", 0)
    cost = calculate_cost(tokens_in, tokens_out, reasoning_tokens)
    footer = format_footer(
        settings.xai_model_reasoning,
        tokens_in,
        tokens_out,
        reasoning_tokens,
        cost,
        elapsed,
    )
    final_text = f"{escape_html(full_content)}\n\n<code>{escape_html(footer)}</code>"
    parts = split_message(final_text, max_length=4000)

    try:
        await sent.edit_text(parts[0], parse_mode="HTML")
    except Exception:
        pass
    for part in parts[1:]:
        try:
            await update.message.reply_text(part, parse_mode="HTML")
        except Exception:
            logger.exception("file_send_part_failed", user_id=user_id)

    await save_message(user_id, "user", f"[{source_label}] {prompt}")
    await save_message(
        user_id,
        "assistant",
        full_content,
        reasoning_content=full_reasoning,
        model=settings.xai_model_reasoning,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        reasoning_tokens=reasoning_tokens,
        cost_usd=cost,
    )
    await update_daily_stats(user_id, tokens_in, tokens_out, reasoning_tokens, cost)


async def _process_document_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    source_message: Message,
    prompt_override: str | None = None,
) -> None:
    """Handle a Telegram document message."""
    if not update.message or not source_message.document:
        return

    filename = source_message.document.file_name or "plik"
    prompt = prompt_override or (update.message.caption or "").strip() or _DEFAULT_FILE_PROMPT
    file_type = detect_file_type(filename)

    try:
        telegram_file = await context.bot.get_file(source_message.document.file_id)
        file_bytes = bytes(await telegram_file.download_as_bytearray())
    except Exception as exc:
        logger.error("document_download_failed", filename=filename, error=str(exc))
        await update.message.reply_text("❌ Nie udało się pobrać pliku z Telegrama.")
        return

    if file_type == "image":
        await analyze_image_bytes(update, context, file_bytes, prompt, source_label=f"file:{filename}")
        return

    if file_type == "pdf":
        try:
            extracted = await extract_text_from_pdf(file_bytes)
        except Exception as exc:
            logger.error("pdf_extract_failed", filename=filename, error=str(exc))
            await update.message.reply_text("❌ Nie udało się odczytać PDF.")
            return
    elif file_type == "docx":
        try:
            extracted = await extract_text_from_docx(file_bytes)
        except Exception as exc:
            logger.error("docx_extract_failed", filename=filename, error=str(exc))
            await update.message.reply_text("❌ Nie udało się odczytać DOCX.")
            return
    elif file_type == "zip":
        try:
            files = await extract_text_from_zip(file_bytes)
        except Exception as exc:
            logger.error("zip_extract_failed", filename=filename, error=str(exc))
            await update.message.reply_text("❌ Nie udało się odczytać ZIP.")
            return
        if not files:
            await update.message.reply_text("❌ ZIP nie zawiera obsługiwanych plików tekstowych.")
            return
        extracted = "\n".join([f"- {name}" for name in files])
        extracted += "\n\n"
        extracted += "\n\n".join([f"### {name}\n{content}" for name, content in files.items()])
    elif file_type == "text":
        extracted = _text_from_bytes(file_bytes)
    else:
        await update.message.reply_text("❌ Nieobsługiwany format pliku.")
        return

    if not extracted.strip():
        await update.message.reply_text("❌ Nie udało się wyciągnąć treści z pliku.")
        return

    await _analyze_text_payload(
        update,
        context,
        prompt=prompt,
        payload=extracted,
        source_label=f"file:{filename}",
    )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Auto-analyze uploaded document files."""
    if not update.effective_user or not update.message or not update.message.document:
        return
    if not await check_access(update, settings):
        return
    await _process_document_message(update, context, update.message)


async def file_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /file as reply to a document."""
    if not update.effective_user or not update.message:
        return
    if not await check_access(update, settings):
        return

    reply = update.message.reply_to_message
    if not reply or not reply.document:
        await update.message.reply_text("Użycie: odpowiedz /file na wiadomość z plikiem.")
        return

    prompt_override = " ".join(context.args).strip() if context.args else None
    await _process_document_message(update, context, reply, prompt_override=prompt_override)
