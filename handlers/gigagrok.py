"""GigaGrok full-power handler (/gigagrok) — web/X/code tools."""

from __future__ import annotations

import time
from typing import Any

import structlog
from telegram import Message, Update
from telegram.ext import ContextTypes

from config import settings
from db import calculate_cost, get_history, save_message, update_daily_stats
from file_utils import image_to_base64
from grok_client import GrokClient
from tools import build_stage2_tools
from utils import (
    check_access,
    escape_html,
    format_gigagrok_footer,
    get_current_date,
    markdown_to_telegram_html,
    split_message,
)

logger = structlog.get_logger(__name__)

_TOOL_STATUS: dict[str, str] = {
    "web_search": "🌐 Szukam w internecie...",
    "x_search": "🐦 Sprawdzam X/Twitter...",
    "code_interpreter": "⚡ Uruchamiam kod...",
    "code_execution": "⚡ Uruchamiam kod...",
    "file_search": "📚 Szukam w kolekcji...",
    "collections_search": "📚 Szukam w kolekcjach...",
}


def _extract_reply_text(message: Message | None) -> str:
    """Extract textual reply context when available."""
    if message is None:
        return ""
    return (message.text or message.caption or "").strip()


async def _build_user_message_content(
    context: ContextTypes.DEFAULT_TYPE,
    reply: Message | None,
    prompt: str,
) -> str | list[dict[str, Any]]:
    """Build user content, optionally multimodal when reply includes image."""
    if not reply:
        return prompt

    context_text = _extract_reply_text(reply)
    final_prompt = (
        f"Kontekst:\n{context_text}\n\nZapytanie:\n{prompt}" if context_text else prompt
    )

    is_image_document = bool(
        reply.document and (reply.document.mime_type or "").startswith("image/")
    )
    if not reply.photo and not is_image_document:
        return final_prompt

    try:
        if reply.photo:
            telegram_file = await context.bot.get_file(reply.photo[-1].file_id)
        elif reply.document:
            telegram_file = await context.bot.get_file(reply.document.file_id)
        else:
            return final_prompt
        file_bytes = bytes(await telegram_file.download_as_bytearray())
        image_b64, mime_type = await image_to_base64(file_bytes)
        return [
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{image_b64}"},
            },
            {"type": "text", "text": final_prompt},
        ]
    except Exception:
        logger.exception("gigagrok_reply_image_prepare_failed")
        return final_prompt


async def gigagrok_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /gigagrok <prompt> in full-power autonomous mode.

    Uses web / X / code tools. Collection search is separate: /collectionsearch.
    """
    if not update.effective_user or not update.message:
        return
    if not await check_access(update, settings):
        return

    user_id = update.effective_user.id
    prompt = " ".join(context.args).strip() if context.args else ""
    reply = update.message.reply_to_message
    if reply and not prompt:
        prompt = "Przeanalizuj powyższe."
    if not prompt:
        await update.message.reply_text("Podaj prompt po /gigagrok")
        return

    grok: GrokClient | None = context.application.bot_data.get("grok_client")
    if grok is None:
        await update.message.reply_text("❌ Klient Grok nie został zainicjalizowany.")
        return

    # Build tools for the combined request (no file_search — use /collectionsearch)
    tools: list[dict[str, Any]] = []
    if settings.gigagrok_stage2_enabled:
        tools.extend(build_stage2_tools())

    system_prompt = (
        "Jesteś w trybie GIGAGROK — PEŁNA MOC.\n\n"
        "Masz dostęp do narzędzi:\n"
        "🌐 Web Search — szukaj aktualnych informacji w internecie\n"
        "🐦 X Search — szukaj na X/Twitter\n"
        "⚡ Code Interpreter — uruchamiaj kod w sandboxie\n\n"
        "STRATEGIA:\n"
        "1. Użyj web/X search aby uzyskać aktualne informacje.\n"
        "2. Użyj code interpreter gdy potrzebne obliczenia lub analiza.\n"
        "3. Myśl głęboko, ale publikuj TYLKO wynik i wnioski.\n"
        "4. Daj kompletną, praktyczną odpowiedź.\n\n"
        f"Aktualna data: {get_current_date()}"
    )

    user_content = await _build_user_message_content(context, reply, prompt)

    # Include recent conversation context
    history = await get_history(user_id, limit=settings.gigagrok_context_messages)
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for msg in history:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": user_content})

    sent = await update.message.reply_text(
        "🚀 <b>GIGAGROK MODE</b>\n🔄 Przetwarzam…",
        parse_mode="HTML",
    )
    start_time = time.time()
    full_content = ""
    full_reasoning = ""
    usage: dict[str, int] = {}
    used_tools: list[str] = []
    last_edit = 0.0

    try:
        async for event_type, data in grok.chat_stream(
            messages=messages,
            model=settings.xai_model_reasoning,
            max_tokens=settings.gigagrok_max_output_tokens,
            tools=tools if tools else None,
            search={"search": {"enabled": True}},
        ):
            if event_type == "reasoning":
                full_reasoning += str(data)
            elif event_type == "tool_use":
                tool_name = str(data)
                if tool_name and tool_name not in used_tools:
                    used_tools.append(tool_name)
                status_text = _TOOL_STATUS.get(tool_name, f"🛠 Używam: {tool_name}")
                try:
                    await sent.edit_text(
                        "🚀 <b>GIGAGROK MODE</b>\n" f"{escape_html(status_text)}",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
            elif event_type == "content":
                full_content += str(data)
                now = time.time()
                if now - last_edit > 1.5:
                    display = markdown_to_telegram_html(full_content[:3800])
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
        logger.error("gigagrok_command_api_error", user_id=user_id, error=str(exc))
        await sent.edit_text(f"❌ Błąd API: {escape_html(str(exc))}", parse_mode="HTML")
        return

    # Update status to final
    try:
        await sent.edit_text(
            "🚀 <b>GIGAGROK MODE</b>\n✅ Final…",
            parse_mode="HTML",
        )
    except Exception:
        pass

    elapsed = time.time() - start_time
    tokens_in = int(usage.get("prompt_tokens", 0) or 0)
    tokens_out = int(usage.get("completion_tokens", 0) or 0)
    reasoning_tokens = int(usage.get("reasoning_tokens", 0) or 0)
    cost = calculate_cost(tokens_in, tokens_out, reasoning_tokens)
    footer = format_gigagrok_footer(
        settings.xai_model_reasoning,
        tokens_in,
        tokens_out,
        reasoning_tokens,
        cost,
        elapsed,
        used_tools,
    )
    final_text = f"{markdown_to_telegram_html(full_content)}\n\n<code>{escape_html(footer)}</code>"
    parts = split_message(final_text, max_length=4000)

    try:
        await sent.edit_text(parts[0], parse_mode="HTML")
    except Exception:
        pass
    for part in parts[1:]:
        try:
            await update.message.reply_text(part, parse_mode="HTML")
        except Exception:
            logger.exception("gigagrok_send_part_failed", user_id=user_id)

    await save_message(user_id, "user", prompt)
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

    logger.info(
        "gigagrok_command_complete",
        user_id=user_id,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        reasoning_tokens=reasoning_tokens,
        tools_used=used_tools,
        cost=cost,
        elapsed=round(elapsed, 2),
    )
