"""Collection handlers (/collection) for local SQLite collections."""

from __future__ import annotations

from typing import Any

import structlog
from telegram import Update
from telegram.ext import ContextTypes

from config import settings
from db import (
    add_local_collection_document,
    create_local_collection,
    delete_local_collection,
    list_local_collection_documents,
    list_local_collections,
    search_local_collection_documents,
)
from file_utils import detect_file_type, extract_text_from_docx, extract_text_from_pdf, extract_text_from_zip
from utils import check_access, escape_html

logger = structlog.get_logger(__name__)
MAX_SNIPPET_DISPLAY_LENGTH = 280


def _plural_pl(count: int, form_1: str, form_2_4: str, form_other: str) -> str:
    """Return Polish plural form for integer count."""
    if count == 1:
        return form_1
    mod10 = count % 10
    mod100 = count % 100
    if mod10 in (2, 3, 4) and mod100 not in (12, 13, 14):
        return form_2_4
    return form_other


def _decode_text_bytes(data: bytes) -> str:
    """Decode plain text bytes safely."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return data.decode("cp1250")
        except UnicodeDecodeError:
            return data.decode("latin-1", errors="replace")


async def _extract_document_text(filename: str, file_bytes: bytes) -> str | None:
    """Extract text from supported document types for local fallback."""
    file_type = detect_file_type(filename)
    if file_type == "pdf":
        return await extract_text_from_pdf(file_bytes)
    if file_type == "docx":
        return await extract_text_from_docx(file_bytes)
    if file_type == "zip":
        files = await extract_text_from_zip(file_bytes)
        if not files:
            return ""
        return "\n\n".join([f"### {name}\n{content}" for name, content in files.items()])
    if file_type == "text":
        return _decode_text_bytes(file_bytes)
    return None


async def _show_menu(update: Update) -> None:
    """Show local collections list and command help."""
    if not update.message:
        return

    local_items = await list_local_collections()
    collections: list[dict[str, Any]] = []
    for item in local_items:
        collections.append(
            {
                "id": f"local_{int(item['id'])}",
                "name": str(item["name"]),
                "document_count": int(item.get("document_count", 0)),
            }
        )

    count = len(collections)
    noun = _plural_pl(count, "kolekcja", "kolekcje", "kolekcji")
    lines: list[str] = [f"📚 <b>Kolekcje lokalne</b> ({count} {noun})", ""]
    for idx, item in enumerate(collections, start=1):
        document_count = int(item.get("document_count", 0))
        doc_noun = _plural_pl(document_count, "dokument", "dokumenty", "dokumentów")
        lines.append(
            f"{idx}. 📁 {escape_html(str(item['name']))} "
            f"(<code>{escape_html(str(item['id']))}</code>) — "
            f"{document_count} {doc_noun}"
        )
    if not collections:
        lines.append("Brak kolekcji.")

    if settings.xai_collection_id:
        lines.extend([
            "",
            f"🌐 <b>Kolekcja xAI:</b> <code>{escape_html(settings.xai_collection_id)}</code>",
            "  (zarządzana ręcznie na stronie xAI, używana automatycznie w /gigagrok)",
        ])

    lines.extend(
        [
            "",
            "Komendy (lokalne kolekcje):",
            "/collection create &lt;nazwa&gt;",
            "/collection add &lt;id&gt; (reply na plik)",
            "/collection search &lt;id&gt; &lt;query&gt;",
            "/collection list &lt;id&gt;",
            "/collection delete &lt;id&gt;",
        ]
    )
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def _create_collection(update: Update, name: str) -> None:
    """Create a local collection."""
    if not update.message:
        return

    local_id = await create_local_collection(name)
    if local_id is None:
        await update.message.reply_text("❌ Nie udało się utworzyć kolekcji.")
        return
    await update.message.reply_text(
        f"✅ Utworzono lokalną kolekcję: <code>local_{local_id}</code>",
        parse_mode="HTML",
    )


def _parse_local_collection_id(collection_id: str) -> int | None:
    """Parse ``local_<id>`` collection format."""
    if not collection_id.startswith("local_"):
        return None
    value = collection_id[6:].strip()
    if not value.isdigit():
        return None
    return int(value)


async def _add_document(update: Update, context: ContextTypes.DEFAULT_TYPE, collection_id: str) -> None:
    """Upload document to a local collection."""
    if not update.message:
        return

    reply = update.message.reply_to_message
    if not reply or not reply.document:
        await update.message.reply_text("Użycie: odpowiedz /collection add <id> na wiadomość z plikiem.")
        return

    local_id = _parse_local_collection_id(collection_id)
    if local_id is None:
        await update.message.reply_text("❌ Podaj ID lokalnej kolekcji w formacie local_<id>.")
        return

    filename = reply.document.file_name or "plik"
    try:
        tg_file = await context.bot.get_file(reply.document.file_id)
        file_bytes = bytes(await tg_file.download_as_bytearray())
    except Exception:
        logger.exception("collection_file_download_failed", collection_id=collection_id, filename=filename)
        await update.message.reply_text("❌ Nie udało się pobrać pliku z Telegrama.")
        return

    extracted = await _extract_document_text(filename, file_bytes)
    if extracted is None:
        await update.message.reply_text("❌ Obsługiwane formaty: txt/md/pdf/docx/zip.")
        return
    if not extracted.strip():
        await update.message.reply_text("❌ Nie udało się wyciągnąć treści z pliku.")
        return
    ok = await add_local_collection_document(local_id, filename, extracted)
    if not ok:
        await update.message.reply_text("❌ Nie udało się dodać dokumentu do kolekcji.")
        return
    await update.message.reply_text("✅ Dodano dokument do lokalnej kolekcji.")


async def _list_documents(update: Update, collection_id: str) -> None:
    """List documents from a local collection."""
    if not update.message:
        return

    local_id = _parse_local_collection_id(collection_id)
    if local_id is None:
        await update.message.reply_text("❌ Podaj ID lokalnej kolekcji w formacie local_<id>.")
        return

    docs = await list_local_collection_documents(local_id)
    if not docs:
        await update.message.reply_text("📄 Brak dokumentów w tej kolekcji.")
        return
    lines = [f"📄 <b>Dokumenty</b> w <code>{escape_html(collection_id)}</code>:", ""]
    for idx, doc in enumerate(docs, start=1):
        lines.append(f"{idx}. {escape_html(str(doc.get('filename', 'plik')))}")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def _delete_collection(update: Update, collection_id: str) -> None:
    """Delete a local collection by ID."""
    if not update.message:
        return

    local_id = _parse_local_collection_id(collection_id)
    if local_id is None:
        await update.message.reply_text("❌ Podaj ID lokalnej kolekcji w formacie local_<id>.")
        return

    removed = await delete_local_collection(local_id)
    if removed <= 0:
        await update.message.reply_text("ℹ️ Taka kolekcja nie istnieje.")
        return
    await update.message.reply_text("🗑️ Usunięto kolekcję.")


async def _search_collection(
    update: Update,
    collection_id: str,
    query: str,
) -> None:
    """Search a local collection via FTS5 or LIKE fallback."""
    if not update.message:
        return

    local_id = _parse_local_collection_id(collection_id)
    if local_id is None:
        await update.message.reply_text("❌ Podaj ID lokalnej kolekcji w formacie local_<id>.")
        return

    results = await search_local_collection_documents(local_id, query)
    if not results:
        await update.message.reply_text("🔎 Brak wyników w kolekcji.")
        return
    lines = [f"🔎 <b>Wyniki</b> dla: <i>{escape_html(query)}</i>", ""]
    for idx, row in enumerate(results, start=1):
        snippet = str(row.get("snippet", "")).strip() or "(brak podglądu)"
        lines.append(
            f"{idx}. <b>{escape_html(str(row.get('filename', 'plik')))}</b>\n"
            f"{escape_html(snippet[:MAX_SNIPPET_DISPLAY_LENGTH])}"
        )
    await update.message.reply_text("\n\n".join(lines), parse_mode="HTML")


async def collection_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /collection command and subcommands."""
    if not update.effective_user or not update.message:
        return
    if not await check_access(update, settings):
        return

    if not context.args:
        await _show_menu(update)
        return

    action = context.args[0].lower().strip()
    args = context.args[1:]

    if action == "create":
        name = " ".join(args).strip()
        if not name:
            await update.message.reply_text("Użycie: /collection create <nazwa>")
            return
        await _create_collection(update, name)
        return

    if action == "add":
        if not args:
            await update.message.reply_text("Użycie: /collection add <id> (reply na plik)")
            return
        await _add_document(update, context, args[0].strip())
        return

    if action == "search":
        if len(args) < 2:
            await update.message.reply_text("Użycie: /collection search <id> <query>")
            return
        collection_id = args[0].strip()
        query = " ".join(args[1:]).strip()
        await _search_collection(update, collection_id, query)
        return

    if action == "list":
        if not args:
            await update.message.reply_text("Użycie: /collection list <id>")
            return
        await _list_documents(update, args[0].strip())
        return

    if action == "delete":
        if not args:
            await update.message.reply_text("Użycie: /collection delete <id>")
            return
        await _delete_collection(update, args[0].strip())
        return

    await update.message.reply_text(
        "Nieznana akcja. Użyj: create, add, search, list, delete."
    )
