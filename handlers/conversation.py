"""Conversation management handlers: /clear, /stats, /system, /think."""

from __future__ import annotations

import time
from typing import Any

import structlog
from telegram import Update
from telegram.ext import ContextTypes

from config import DEFAULT_SYSTEM_PROMPT, PERSONALITY_PROFILES, settings
from db import (
    calculate_cost,
    clear_history,
    get_all_time_stats,
    get_daily_stats,
    get_history,
    get_user_setting,
    save_message,
    set_user_setting,
    update_daily_stats,
)
from grok_client import GrokClient
from utils import (
    check_access,
    escape_html,
    format_footer,
    format_number,
    get_current_date,
    markdown_to_telegram_html,
    split_message,
)

logger = structlog.get_logger(__name__)

_THINK_SYSTEM_ADDON = (
    "\n\nDodatkowe instrukcje jakości:\n"
    "- Jeśli nie jesteś pewien odpowiedzi, wyraźnie to zaznacz.\n"
    "- Nie hallucynuj — jeśli nie masz danych, powiedz że nie wiesz.\n"
    "- Przy faktach podawaj źródła lub zaznaczaj niepewność.\n"
    "- Zadaj pytanie jeśli zapytanie jest niejednoznaczne.\n"
    "- Rozważ różne perspektywy przed odpowiedzią."
)


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /clear — delete conversation history for the user."""
    if not update.effective_user or not update.message:
        return
    if not await check_access(update, settings):
        return

    user_id = update.effective_user.id
    try:
        deleted = await clear_history(user_id)
        logger.info("clear_command", user_id=user_id, deleted=deleted)
        await update.message.reply_text(
            f"🗑 Usunięto <b>{deleted}</b> wiadomości z historii.",
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.error("clear_command_failed", user_id=user_id, error=str(exc))
        await update.message.reply_text("❌ Nie udało się wyczyścić historii.")


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /stats — show usage statistics."""
    if not update.effective_user or not update.message:
        return
    if not await check_access(update, settings):
        return

    user_id = update.effective_user.id
    try:
        today = await get_daily_stats(user_id)
        alltime = await get_all_time_stats(user_id)

        lines = [
            "📊 <b>Statystyki użycia</b>",
            "",
            "📅 <b>Dzisiaj:</b>",
            f"  Zapytania: <b>{today.get('total_requests', 0)}</b>",
            f"  Tokeny IN: <b>{format_number(today.get('total_tokens_in', 0))}</b>",
            f"  Tokeny OUT: <b>{format_number(today.get('total_tokens_out', 0))}</b>",
            f"  Reasoning: <b>{format_number(today.get('total_reasoning_tokens', 0))}</b>",
            f"  Koszt: <b>${today.get('total_cost_usd', 0.0):.4f}</b>",
            "",
            "🌍 <b>Ogółem:</b>",
            f"  Zapytania: <b>{alltime.get('total_requests', 0)}</b>",
            f"  Tokeny IN: <b>{format_number(alltime.get('total_tokens_in', 0))}</b>",
            f"  Tokeny OUT: <b>{format_number(alltime.get('total_tokens_out', 0))}</b>",
            f"  Reasoning: <b>{format_number(alltime.get('total_reasoning_tokens', 0))}</b>",
            f"  Koszt: <b>${alltime.get('total_cost_usd', 0.0):.4f}</b>",
        ]
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
        logger.info("stats_command", user_id=user_id)
    except Exception as exc:
        logger.error("stats_command_failed", user_id=user_id, error=str(exc))
        await update.message.reply_text("❌ Nie udało się pobrać statystyk.")


async def system_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /system — show or set custom system prompt.

    /system         → show current prompt
    /system reset   → reset to default
    /system <text>  → set custom prompt
    """
    if not update.effective_user or not update.message:
        return
    if not await check_access(update, settings):
        return

    user_id = update.effective_user.id
    args_text = " ".join(context.args).strip() if context.args else ""

    try:
        if not args_text:
            current = await get_user_setting(user_id, "system_prompt")
            if current:
                display = escape_html(current[:2000])
                await update.message.reply_text(
                    f"⚙️ <b>Aktualny system prompt:</b>\n\n{display}",
                    parse_mode="HTML",
                )
            else:
                await update.message.reply_text(
                    "⚙️ Używasz domyślnego system promptu.\n"
                    "Użyj /system &lt;tekst&gt; aby ustawić własny.\n"
                    "Użyj /system reset aby zresetować.",
                    parse_mode="HTML",
                )
            return

        if args_text.lower() == "reset":
            await set_user_setting(user_id, "system_prompt", "")
            await update.message.reply_text("✅ System prompt zresetowany do domyślnego.")
            logger.info("system_command_reset", user_id=user_id)
            return

        await set_user_setting(user_id, "system_prompt", args_text)
        await update.message.reply_text(
            f"✅ Ustawiono nowy system prompt ({len(args_text)} znaków)."
        )
        logger.info("system_command_set", user_id=user_id, length=len(args_text))
    except Exception as exc:
        logger.error("system_command_failed", user_id=user_id, error=str(exc))
        await update.message.reply_text("❌ Nie udało się zmienić system promptu.")


async def think_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /think <text> — deep reasoning mode on grok-4-1-fast-reasoning.

    Does NOT send reasoning_effort param; instead appends quality instructions
    to the system prompt.
    """
    if not update.effective_user or not update.message:
        return
    if not await check_access(update, settings):
        return

    user_id = update.effective_user.id
    query = " ".join(context.args).strip() if context.args else ""
    if not query:
        await update.message.reply_text("Użycie: /think <tekst>")
        return

    grok: GrokClient | None = context.application.bot_data.get("grok_client")
    if grok is None:
        await update.message.reply_text("❌ Klient Grok nie został zainicjalizowany.")
        return

    logger.info("think_command", user_id=user_id, query_len=len(query))
    sent = await update.message.reply_text(
        "🧠 <i>Myślę głęboko...</i>", parse_mode="HTML"
    )
    start_time = time.time()

    try:
        history = await get_history(user_id, limit=settings.max_history)
        custom_prompt = await get_user_setting(user_id, "system_prompt")
        base_prompt = custom_prompt or DEFAULT_SYSTEM_PROMPT.format(
            current_date=get_current_date()
        )
        system_prompt = base_prompt + _THINK_SYSTEM_ADDON

        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        for msg in history:
            messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": query})

        full_content = ""
        full_reasoning = ""
        usage: dict[str, int] = {}
        last_edit = 0.0

        async for event_type, data in grok.chat_stream(
            messages=messages,
            model=settings.xai_model_reasoning,
            max_tokens=settings.max_output_tokens,
        ):
            if event_type == "reasoning":
                full_reasoning += str(data)
                now = time.time()
                if now - last_edit > 2.0:
                    try:
                        await sent.edit_text(
                            f"🧠 <i>Myślę głęboko... ({len(full_reasoning)} znaków reasoning)</i>",
                            parse_mode="HTML",
                        )
                    except Exception:
                        pass
                    last_edit = now
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
        logger.error("think_command_api_error", user_id=user_id, error=str(exc))
        await sent.edit_text(
            f"❌ Błąd API: {escape_html(str(exc))}", parse_mode="HTML"
        )
        return

    elapsed = time.time() - start_time
    tokens_in = int(usage.get("prompt_tokens", 0) or 0)
    tokens_out = int(usage.get("completion_tokens", 0) or 0)
    reasoning_tokens = int(usage.get("reasoning_tokens", 0) or 0)
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
            logger.exception("think_command_send_part_failed", user_id=user_id)

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
        "think_command_complete",
        user_id=user_id,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        reasoning_tokens=reasoning_tokens,
        cost=cost,
        elapsed=round(elapsed, 2),
    )


async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /profile — manage personality profiles.

    /profile list          → show available profiles
    /profile <name>        → activate profile as system prompt
    /profile reset         → reset to default
    """
    if not update.effective_user or not update.message:
        return
    if not await check_access(update, settings):
        return

    user_id = update.effective_user.id
    args_text = " ".join(context.args).strip().lower() if context.args else ""

    try:
        if not args_text or args_text == "list":
            lines = ["🎭 <b>Dostępne profile osobowości:</b>", ""]
            for name, desc in PERSONALITY_PROFILES.items():
                lines.append(f"  • <b>{escape_html(name)}</b> — {escape_html(desc[:80])}…")
            lines.extend(["", "Użyj: /profile &lt;nazwa&gt; aby aktywować"])
            lines.append("Użyj: /profile reset aby wrócić do domyślnego")
            await update.message.reply_text("\n".join(lines), parse_mode="HTML")
            return

        if args_text == "reset":
            await set_user_setting(user_id, "system_prompt", "")
            await update.message.reply_text("✅ Profil zresetowany do domyślnego.")
            logger.info("profile_reset", user_id=user_id)
            return

        profile_prompt = PERSONALITY_PROFILES.get(args_text)
        if profile_prompt is None:
            available = ", ".join(PERSONALITY_PROFILES.keys())
            await update.message.reply_text(
                f"❌ Nieznany profil: <code>{escape_html(args_text)}</code>\n"
                f"Dostępne: {escape_html(available)}",
                parse_mode="HTML",
            )
            return

        full_prompt = DEFAULT_SYSTEM_PROMPT.format(current_date=get_current_date()) + "\n\n" + profile_prompt
        await set_user_setting(user_id, "system_prompt", full_prompt)
        await update.message.reply_text(
            f"✅ Aktywowano profil: <b>{escape_html(args_text)}</b>",
            parse_mode="HTML",
        )
        logger.info("profile_set", user_id=user_id, profile=args_text)
    except Exception as exc:
        logger.error("profile_command_failed", user_id=user_id, error=str(exc))
        await update.message.reply_text("❌ Nie udało się zmienić profilu.")
