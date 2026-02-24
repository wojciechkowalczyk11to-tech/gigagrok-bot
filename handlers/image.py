"""Image handlers for multimodal Grok analysis."""

from __future__ import annotations

import time
from typing import Any

import structlog
from telegram import Message, Update
from telegram.ext import ContextTypes

from config import settings
from db import calculate_cost, save_message, update_daily_stats
from file_utils import image_to_base64
from grok_client import GrokClient
from utils import check_access, escape_html, format_footer, split_message

logger = structlog.get_logger(__name__)

_DEFAULT_IMAGE_PROMPT = (
    "Szczegółowo opisz i przeanalizuj ten obraz. Co widzisz? Jakie wnioski?"
)


async def analyze_image_bytes(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    file_bytes: bytes,
    prompt: str,
    source_label: str = "photo",
) -> None:
    """Run multimodal analysis for a single image payload."""
    if not update.effective_user or not update.message:
        return
    user_id = update.effective_user.id

    grok: GrokClient | None = context.application.bot_data.get("grok_client")
    if grok is None:
        await update.message.reply_text("❌ Klient Grok nie został zainicjalizowany.")
        return

    try:
        image_b64, mime_type = await image_to_base64(file_bytes)
    except Exception as exc:
        logger.error("image_convert_failed", user_id=user_id, error=str(exc))
        await update.message.reply_text(
            f"❌ Nie udało się przetworzyć obrazu: {escape_html(str(exc))}",
            parse_mode="HTML",
        )
        return

    sent = await update.message.reply_text("🖼 <i>Analizuję obraz...</i>", parse_mode="HTML")
    start_time = time.time()
    full_content = ""
    full_reasoning = ""
    usage: dict[str, int] = {}
    last_edit = 0.0

    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime_type};base64,{image_b64}"},
                },
                {"type": "text", "text": prompt},
            ],
        }
    ]

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
        logger.error("image_analysis_failed", user_id=user_id, error=str(exc))
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
            logger.exception("image_send_part_failed", user_id=user_id)

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


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Auto-analyze incoming photos."""
    if not update.effective_user or not update.message or not update.message.photo:
        return
    if not await check_access(update, settings):
        return

    prompt = (update.message.caption or "").strip() or _DEFAULT_IMAGE_PROMPT
    try:
        telegram_file = await context.bot.get_file(update.message.photo[-1].file_id)
        file_bytes = bytes(await telegram_file.download_as_bytearray())
    except Exception as exc:
        logger.error("photo_download_failed", error=str(exc))
        await update.message.reply_text("❌ Nie udało się pobrać zdjęcia z Telegrama.")
        return
    await analyze_image_bytes(update, context, file_bytes, prompt, source_label="photo")


def _extract_image_message(message: Message | None) -> Message | None:
    """Return message if it contains a photo-like payload."""
    if not message:
        return None
    if message.photo:
        return message
    if message.document and (message.document.mime_type or "").startswith("image/"):
        return message
    return None


async def image_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /image as a reply to a photo/document."""
    if not update.effective_user or not update.message:
        return
    if not await check_access(update, settings):
        return

    prompt = " ".join(context.args).strip() or _DEFAULT_IMAGE_PROMPT
    source_message = _extract_image_message(update.message.reply_to_message)
    if source_message is None:
        await update.message.reply_text("Użycie: odpowiedz /image na wiadomość ze zdjęciem.")
        return

    try:
        if source_message.photo:
            telegram_file = await context.bot.get_file(source_message.photo[-1].file_id)
        elif source_message.document:
            telegram_file = await context.bot.get_file(source_message.document.file_id)
        else:
            await update.message.reply_text("❌ Nie znaleziono obrazu w odpowiedzi.")
            return
        file_bytes = bytes(await telegram_file.download_as_bytearray())
    except Exception as exc:
        logger.error("image_command_download_failed", error=str(exc))
        await update.message.reply_text("❌ Nie udało się pobrać obrazu z Telegrama.")
        return

    await analyze_image_bytes(update, context, file_bytes, prompt, source_label="image")
