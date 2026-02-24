"""Main chat message handler for GigaGrok Bot."""

from __future__ import annotations

import time
from typing import Any

import structlog
from telegram import Update
from telegram.ext import ContextTypes

from config import DEFAULT_SYSTEM_PROMPT, settings
from db import calculate_cost, get_history, save_message, update_daily_stats, get_user_setting
from grok_client import GrokClient
from utils import check_access, escape_html, format_footer, get_current_date, split_message

logger = structlog.get_logger(__name__)

# Module-level client — initialised in main.py via ``init_grok_client``
_grok: GrokClient | None = None


def init_grok_client(client: GrokClient) -> None:
    """Store the shared :class:`GrokClient` instance for this module."""
    global _grok  # noqa: PLW0603
    _grok = client


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Process an incoming text message: stream a Grok response back."""
    if not update.effective_user or not update.message or not update.message.text:
        return

    if not await check_access(update, settings):
        return
    user_id = update.effective_user.id

    if _grok is None:
        await update.message.reply_text("❌ Klient Grok nie został zainicjalizowany.")
        return

    query = update.message.text
    logger.info("handle_message", user_id=user_id, query_len=len(query))

    # 2. History
    history = await get_history(user_id, limit=settings.max_history)

    # 3. System prompt
    custom_prompt = await get_user_setting(user_id, "system_prompt")
    system_prompt = custom_prompt or DEFAULT_SYSTEM_PROMPT.format(
        current_date=get_current_date()
    )

    # 4. Build messages
    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    for msg in history:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": query})

    # 5. Placeholder
    sent = await update.message.reply_text(
        "🧠 <i>Grok myśli...</i>", parse_mode="HTML"
    )

    # 6. Stream
    start_time = time.time()
    full_content = ""
    full_reasoning = ""
    usage: dict[str, int] = {}
    last_edit = 0.0

    try:
        async for event_type, data in _grok.chat_stream(
            messages,
            model=settings.xai_model_reasoning,
            max_tokens=settings.max_output_tokens,
            reasoning_effort=settings.default_reasoning_effort,
        ):
            if event_type == "reasoning":
                full_reasoning += data
                now = time.time()
                if now - last_edit > 2.0:
                    try:
                        await sent.edit_text(
                            f"🧠 <i>Grok myśli... ({len(full_reasoning)} znaków reasoning)</i>",
                            parse_mode="HTML",
                        )
                    except Exception:
                        pass
                    last_edit = now

            elif event_type == "content":
                full_content += data
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
                usage = data

    except Exception as exc:
        logger.error("grok_api_error", error=str(exc))
        await sent.edit_text(
            f"❌ Błąd API: {escape_html(str(exc))}", parse_mode="HTML"
        )
        return

    # 7. Footer
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

    # 8. Final message (split if needed)
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
            logger.exception("send_part_failed")

    # 9. Persist
    await save_message(user_id, "user", query)
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
        "message_complete",
        user_id=user_id,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        reasoning_tokens=reasoning_tokens,
        cost=cost,
        elapsed=round(elapsed, 2),
    )
