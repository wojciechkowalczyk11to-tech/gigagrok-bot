"""Mode command handlers (/fast) for GigaGrok Bot."""

from __future__ import annotations

import time
from typing import Any

import structlog
from telegram import Update
from telegram.ext import ContextTypes

from config import DEFAULT_SYSTEM_PROMPT, settings
from db import calculate_cost, get_history, get_user_setting, save_message, update_daily_stats
from grok_client import GrokClient
from utils import check_access, escape_html, format_footer, get_current_date, markdown_to_telegram_html, split_message

logger = structlog.get_logger(__name__)


async def fast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /fast <text> - quick response without reasoning."""
    if not update.effective_user or not update.message:
        return
    if not await check_access(update, settings):
        return

    user_id = update.effective_user.id
    query = " ".join(context.args).strip()
    if not query:
        await update.message.reply_text("Użycie: /fast <tekst>")
        return

    grok: GrokClient | None = context.application.bot_data.get("grok_client")
    if grok is None:
        await update.message.reply_text("❌ Klient Grok nie został zainicjalizowany.")
        return

    logger.info("fast_command", user_id=user_id, query_len=len(query))
    sent = await update.message.reply_text("⚡ <i>Generuję szybką odpowiedź...</i>", parse_mode="HTML")
    start_time = time.time()

    try:
        history = await get_history(user_id, limit=settings.max_history)
        custom_prompt = await get_user_setting(user_id, "system_prompt")
        system_prompt = custom_prompt or DEFAULT_SYSTEM_PROMPT.format(
            current_date=get_current_date()
        )

        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        for msg in history:
            messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": query})

        response: dict[str, Any] = await grok.chat(
            messages=messages,
            model=settings.xai_model_fast,
            max_tokens=settings.max_output_tokens,
        )
    except Exception as exc:
        logger.error("fast_command_api_error", user_id=user_id, error=str(exc))
        await sent.edit_text(f"❌ Błąd API: {escape_html(str(exc))}", parse_mode="HTML")
        return

    choices = response.get("choices", [])
    message = choices[0].get("message", {}) if choices else {}
    content = message.get("content") or "❌ Brak odpowiedzi z modelu."
    usage = response.get("usage", {})

    tokens_in = int(usage.get("prompt_tokens", 0) or 0)
    tokens_out = int(usage.get("completion_tokens", 0) or 0)
    reasoning_tokens = 0
    cost = calculate_cost(tokens_in, tokens_out, reasoning_tokens)
    elapsed = time.time() - start_time

    footer = format_footer(
        settings.xai_model_fast,
        tokens_in,
        tokens_out,
        reasoning_tokens,
        cost,
        elapsed,
    )

    final_text = f"{markdown_to_telegram_html(content)}\n\n<code>{escape_html(footer)}</code>"
    parts = split_message(final_text, max_length=4000)

    try:
        await sent.edit_text(parts[0], parse_mode="HTML")
    except Exception:
        pass

    for part in parts[1:]:
        try:
            await update.message.reply_text(part, parse_mode="HTML")
        except Exception:
            logger.exception("fast_command_send_part_failed", user_id=user_id)

    await save_message(user_id, "user", query)
    await save_message(
        user_id,
        "assistant",
        content,
        model=settings.xai_model_fast,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        reasoning_tokens=reasoning_tokens,
        cost_usd=cost,
    )
    await update_daily_stats(user_id, tokens_in, tokens_out, reasoning_tokens, cost)

    logger.info(
        "fast_command_complete",
        user_id=user_id,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost=cost,
        elapsed=round(elapsed, 2),
    )
