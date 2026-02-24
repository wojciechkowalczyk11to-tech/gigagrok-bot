"""GitHub/workspace command handlers for phase 8."""

from __future__ import annotations

from pathlib import Path

import structlog
from telegram import Update
from telegram.ext import ContextTypes

from config import settings
from github_client import GitHubClient
from utils import check_access

logger = structlog.get_logger(__name__)

_PENDING_FILE_KEY = "pending_workspace_file_context"


async def github_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /github <file> and attach file content to next query."""
    if not update.effective_user or not update.message:
        return
    if not await check_access(update, settings):
        return

    file_path = " ".join(context.args).strip()
    if not file_path:
        await update.message.reply_text("Użycie: /github <plik_w_workspace>")
        return

    client = GitHubClient(workspace_dir=settings.workspace_base)
    try:
        content = await client.read_file(Path(settings.workspace_base), file_path)
    except FileNotFoundError:
        await update.message.reply_text("❌ Plik nie istnieje.")
        return
    except Exception as exc:
        await update.message.reply_text(f"❌ Nie udało się odczytać pliku: {exc}")
        return

    context.user_data[_PENDING_FILE_KEY] = f"### {file_path}\n{content}"
    logger.info("github_file_attached", user_id=update.effective_user.id, file=file_path)
    await update.message.reply_text(
        f"✅ Dodano <code>{file_path}</code> do następnego zapytania.",
        parse_mode="HTML",
    )


async def workspace_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /workspace write <file> command."""
    if not update.effective_user or not update.message:
        return
    if not await check_access(update, settings):
        return

    if len(context.args) < 2 or context.args[0].lower() != "write":
        await update.message.reply_text("Użycie: /workspace write <plik>")
        return

    file_path = " ".join(context.args[1:]).strip()
    if not file_path:
        await update.message.reply_text("❌ Podaj ścieżkę pliku.")
        return

    source = update.message.reply_to_message
    content = ""
    if source:
        if source.text:
            content = source.text
        elif source.caption:
            content = source.caption
    if not content:
        await update.message.reply_text("❌ Odpowiedz na wiadomość z treścią do zapisu.")
        return

    client = GitHubClient(workspace_dir=settings.workspace_base)
    try:
        await client.write_file(Path(settings.workspace_base), file_path, content)
    except Exception as exc:
        await update.message.reply_text(f"❌ Nie udało się zapisać pliku: {exc}")
        return

    logger.info("workspace_file_written", user_id=update.effective_user.id, file=file_path)
    await update.message.reply_text(f"✅ Zapisano plik: <code>{file_path}</code>", parse_mode="HTML")
