"""Search command handlers (/websearch, /xsearch) for GigaGrok Bot."""

from __future__ import annotations

import time

import structlog
from telegram import Update
from telegram.ext import ContextTypes

from config import settings
from db import calculate_cost, save_message, update_daily_stats
from grok_client import GrokClient
from utils import check_access, escape_html, format_footer, markdown_to_telegram_html, split_message

logger = structlog.get_logger(__name__)


async def websearch_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /websearch <query> using xAI server-side web_search tool."""
    query = " ".join(context.args).strip()
    if not query:
        if update.message:
            await update.message.reply_text("Użycie: /websearch <query>")
        return
    system_prompt = (
        "Przeszukaj internet i podaj aktualne, szczegółowe informacje na temat: "
        f"{query}. Cytuj źródła z URL. Strukturyzuj odpowiedź."
    )
    await _run_search_command(
        update=update,
        context=context,
        query=query,
        system_prompt=system_prompt,
        status_text="🔍 Szukam w internecie...",
        search_params={"search": {"enabled": True}},
        command_name="websearch_command",
    )


async def xsearch_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /xsearch <query> using xAI server-side x_search tool."""
    query = " ".join(context.args).strip()
    if not query:
        if update.message:
            await update.message.reply_text("Użycie: /xsearch <query>")
        return
    system_prompt = (
        "Przeszukaj X/Twitter i podaj najnowsze posty i dyskusje na temat: "
        f"{query}. Podaj autorów (@handle), daty i treść. Podsumuj nastroje/trendy."
    )
    await _run_search_command(
        update=update,
        context=context,
        query=query,
        system_prompt=system_prompt,
        status_text="🐦 Szukam na X/Twitter...",
        search_params={"search": {"enabled": True}},
        command_name="xsearch_command",
    )


async def _run_search_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    query: str,
    system_prompt: str,
    status_text: str,
    search_params: dict | None = None,
    command_name: str = "",
) -> None:
    """Execute a streaming search command with a dedicated tool."""
    if not update.effective_user or not update.message:
        return
    if not await check_access(update, settings):
        return

    user_id = update.effective_user.id
    grok: GrokClient | None = context.application.bot_data.get("grok_client")
    if grok is None:
        await update.message.reply_text("❌ Klient Grok nie został zainicjalizowany.")
        return

    logger.info(command_name, user_id=user_id, query_len=len(query))

    sent = await update.message.reply_text(f"{status_text}", parse_mode="HTML")
    start_time = time.time()
    full_content = ""
    full_reasoning = ""
    usage: dict[str, int] = {}
    last_edit = 0.0

    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": query},
    ]

    try:
        async for event_type, data in grok.chat_stream(
            messages=messages,
            model=settings.xai_model_reasoning,
            max_tokens=settings.max_output_tokens,
            reasoning_effort="medium",
            search=search_params,
        ):
            if event_type == "reasoning":
                full_reasoning += str(data)
            elif event_type == "tool_use":
                try:
                    await sent.edit_text(
                        f"{status_text}\n\n🛠 Używam narzędzia: <code>{escape_html(str(data))}</code>",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
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
        logger.error(f"{command_name}_api_error", user_id=user_id, error=str(exc))
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
            logger.exception(f"{command_name}_send_part_failed", user_id=user_id)

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
        f"{command_name}_complete",
        user_id=user_id,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        reasoning_tokens=reasoning_tokens,
        cost=cost,
        elapsed=round(elapsed, 2),
    )
