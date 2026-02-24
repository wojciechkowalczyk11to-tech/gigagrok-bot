"""Collection search handler (/collectionsearch) — REST-based xAI document search."""

from __future__ import annotations

import time
from typing import Any

import structlog
from telegram import Update
from telegram.ext import ContextTypes

from config import settings
from grok_client import GrokClient
from utils import check_access, escape_html, markdown_to_telegram_html, split_message

logger = structlog.get_logger(__name__)


def _format_results(results: list[dict[str, Any]], query: str) -> str:
    """Format collection search results into readable Markdown."""
    if not results:
        return f"📚 Brak wyników w kolekcji dla: **{query}**"

    lines: list[str] = [f"📚 **Wyniki z kolekcji** ({len(results)}) — *{query}*\n"]
    for i, result in enumerate(results, 1):
        content = result.get("content", result.get("text", "")).strip()
        score = result.get("score", result.get("relevance_score"))
        doc_name = result.get("document_name", result.get("filename", ""))

        # Truncate long content
        if len(content) > 500:
            content = content[:500] + "…"

        header = f"**{i}.**"
        if doc_name:
            header += f" 📄 {doc_name}"
        if score is not None:
            header += f" (score: {score:.3f})"

        lines.append(f"{header}\n{content}\n")

    return "\n".join(lines)


async def collectionsearch_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /collectionsearch <query> — search xAI collection via REST API."""
    if not update.effective_user or not update.message:
        return
    if not await check_access(update, settings):
        return

    user_id = update.effective_user.id
    query = " ".join(context.args).strip() if context.args else ""
    if not query:
        await update.message.reply_text(
            "📚 Użycie: <code>/collectionsearch zapytanie</code>\n"
            "Przeszukuje kolekcję dokumentów xAI.",
            parse_mode="HTML",
        )
        return

    collection_id = settings.xai_collection_id
    if not collection_id:
        await update.message.reply_text("❌ Brak skonfigurowanej kolekcji (XAI_COLLECTION_ID).")
        return

    grok: GrokClient | None = context.application.bot_data.get("grok_client")
    if grok is None:
        await update.message.reply_text("❌ Klient Grok nie został zainicjalizowany.")
        return

    sent = await update.message.reply_text("📚 Szukam w kolekcji…")
    start_time = time.time()

    try:
        results = await grok.search_collection(
            collection_id=collection_id,
            query=query,
            max_results=10,
        )
    except Exception as exc:
        logger.error("collectionsearch_api_error", user_id=user_id, error=str(exc))
        await sent.edit_text(
            f"❌ Błąd przeszukiwania kolekcji: {escape_html(str(exc))}",
            parse_mode="HTML",
        )
        return

    elapsed = time.time() - start_time
    body = _format_results(results, query)
    footer = f"\n\n<code>📚 kolekcja | ⏱ {elapsed:.1f}s | wyników: {len(results)}</code>"
    final_text = f"{markdown_to_telegram_html(body)}{footer}"

    parts = split_message(final_text, max_length=4000)
    try:
        await sent.edit_text(parts[0], parse_mode="HTML")
    except Exception:
        pass
    for part in parts[1:]:
        try:
            await update.message.reply_text(part, parse_mode="HTML")
        except Exception:
            logger.exception("collectionsearch_send_part_failed", user_id=user_id)

    logger.info(
        "collectionsearch_complete",
        user_id=user_id,
        query=query,
        results_count=len(results),
        elapsed=round(elapsed, 2),
    )
