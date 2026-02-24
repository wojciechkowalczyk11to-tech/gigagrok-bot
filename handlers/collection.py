"""Collection handlers (/collection) for xAI Collections API with local fallback."""

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
from grok_client import GrokClient
from utils import check_access, escape_html

logger = structlog.get_logger(__name__)


def _parse_local_collection_id(collection_id: str) -> int | None:
    """Parse ``local_<id>`` collection format."""
    if not collection_id.startswith("local_"):
        return None
    value = collection_id[6:].strip()
    if not value.isdigit():
        return None
    return int(value)


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


async def _show_menu(update: Update, grok: GrokClient) -> None:
    """Show collections list and command help."""
    if not update.message:
        return

    collections: list[dict[str, Any]] = []
    using_local = False
    try:
        remote_collections = await grok.list_collections()
        for collection in remote_collections:
            collection_id = str(collection.get("id", ""))
            if not collection_id:
                continue
            try:
                docs = await grok.list_collection_documents(collection_id)
                doc_count = len(docs)
            except Exception:
                doc_count = 0
            collections.append(
                {
                    "id": collection_id,
                    "name": str(collection.get("name", "Bez nazwy")),
                    "document_count": doc_count,
                }
            )
    except Exception:
        using_local = True
        local_items = await list_local_collections()
        for item in local_items:
            collections.append(
                {
                    "id": f"local_{int(item['id'])}",
                    "name": str(item["name"]),
                    "document_count": int(item.get("document_count", 0)),
                }
            )

    lines: list[str] = [f"📚 <b>Kolekcje</b> ({len(collections)} kolekcje)", ""]
    for idx, item in enumerate(collections, start=1):
        lines.append(
            f"{idx}. 📁 {escape_html(str(item['name']))} "
            f"(<code>{escape_html(str(item['id']))}</code>) — "
            f"{int(item.get('document_count', 0))} dokumentów"
        )
    if not collections:
        lines.append("Brak kolekcji.")
    lines.extend(
        [
            "",
            "Komendy:",
            "/collection create &lt;nazwa&gt;",
            "/collection add &lt;id&gt; (reply na plik)",
            "/collection search &lt;id&gt; &lt;query&gt;",
            "/collection list &lt;id&gt;",
            "/collection delete &lt;id&gt;",
        ]
    )
    if using_local:
        lines.extend(["", "ℹ️ Tryb fallback: lokalne kolekcje SQLite (FTS5)."])
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def _create_collection(update: Update, grok: GrokClient, name: str) -> None:
    """Create collection in xAI, fallback to local collection on failure."""
    if not update.message:
        return

    try:
        response = await grok.create_collection(name)
        collection_id = response.get("id")
        if collection_id:
            await update.message.reply_text(
                f"✅ Utworzono kolekcję: <code>{escape_html(str(collection_id))}</code>",
                parse_mode="HTML",
            )
            return
    except Exception:
        logger.exception("create_remote_collection_failed", name=name)

    local_id = await create_local_collection(name)
    if local_id is None:
        await update.message.reply_text("❌ Nie udało się utworzyć kolekcji.")
        return
    await update.message.reply_text(
        f"✅ Utworzono lokalną kolekcję fallback: <code>local_{local_id}</code>",
        parse_mode="HTML",
    )


async def _add_document(update: Update, context: ContextTypes.DEFAULT_TYPE, grok: GrokClient, collection_id: str) -> None:
    """Upload document to collection (remote or local fallback)."""
    if not update.message:
        return

    reply = update.message.reply_to_message
    if not reply or not reply.document:
        await update.message.reply_text("Użycie: odpowiedz /collection add <id> na wiadomość z plikiem.")
        return

    filename = reply.document.file_name or "plik"
    try:
        tg_file = await context.bot.get_file(reply.document.file_id)
        file_bytes = bytes(await tg_file.download_as_bytearray())
    except Exception:
        logger.exception("collection_file_download_failed", collection_id=collection_id, filename=filename)
        await update.message.reply_text("❌ Nie udało się pobrać pliku z Telegrama.")
        return

    local_id = _parse_local_collection_id(collection_id)
    if local_id is not None:
        extracted = await _extract_document_text(filename, file_bytes)
        if extracted is None:
            await update.message.reply_text("❌ Lokalny fallback obsługuje: txt/md/pdf/docx/zip.")
            return
        if not extracted.strip():
            await update.message.reply_text("❌ Nie udało się wyciągnąć treści z pliku.")
            return
        ok = await add_local_collection_document(local_id, filename, extracted)
        if not ok:
            await update.message.reply_text("❌ Nie udało się dodać dokumentu do lokalnej kolekcji.")
            return
        await update.message.reply_text("✅ Dodano dokument do lokalnej kolekcji.")
        return

    try:
        await grok.upload_collection_document(collection_id, filename, file_bytes, reply.document.mime_type or "application/octet-stream")
        await update.message.reply_text("✅ Dodano dokument do kolekcji xAI.")
    except Exception:
        logger.exception("upload_remote_document_failed", collection_id=collection_id, filename=filename)
        await update.message.reply_text(
            "❌ Upload do xAI nie powiódł się. Jeśli Collections API jest niedostępne, użyj lokalnej kolekcji local_<id>."
        )


async def _list_documents(update: Update, grok: GrokClient, collection_id: str) -> None:
    """List documents from collection."""
    if not update.message:
        return

    local_id = _parse_local_collection_id(collection_id)
    if local_id is not None:
        docs = await list_local_collection_documents(local_id)
        if not docs:
            await update.message.reply_text("📄 Brak dokumentów w tej lokalnej kolekcji.")
            return
        lines = [f"📄 <b>Dokumenty</b> w <code>{escape_html(collection_id)}</code>:", ""]
        for idx, doc in enumerate(docs, start=1):
            lines.append(f"{idx}. {escape_html(str(doc.get('filename', 'plik')))}")
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
        return

    try:
        docs = await grok.list_collection_documents(collection_id)
    except Exception:
        logger.exception("list_remote_documents_failed", collection_id=collection_id)
        await update.message.reply_text("❌ Nie udało się pobrać dokumentów kolekcji.")
        return

    if not docs:
        await update.message.reply_text("📄 Brak dokumentów w tej kolekcji.")
        return
    lines = [f"📄 <b>Dokumenty</b> w <code>{escape_html(collection_id)}</code>:", ""]
    for idx, doc in enumerate(docs, start=1):
        file_name = str(doc.get("filename") or doc.get("name") or doc.get("id") or "plik")
        lines.append(f"{idx}. {escape_html(file_name)}")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def _delete_collection(update: Update, grok: GrokClient, collection_id: str) -> None:
    """Delete collection by ID."""
    if not update.message:
        return

    local_id = _parse_local_collection_id(collection_id)
    if local_id is not None:
        removed = await delete_local_collection(local_id)
        if removed <= 0:
            await update.message.reply_text("ℹ️ Taka lokalna kolekcja nie istnieje.")
            return
        await update.message.reply_text("🗑️ Usunięto lokalną kolekcję.")
        return

    try:
        ok = await grok.delete_collection(collection_id)
    except Exception:
        logger.exception("delete_remote_collection_failed", collection_id=collection_id)
        await update.message.reply_text("❌ Nie udało się usunąć kolekcji xAI.")
        return
    if ok:
        await update.message.reply_text("🗑️ Usunięto kolekcję xAI.")
        return
    await update.message.reply_text("❌ Nie udało się usunąć kolekcji.")


async def _search_collection(
    update: Update,
    grok: GrokClient,
    collection_id: str,
    query: str,
) -> None:
    """Search collection via xAI tool or local fallback."""
    if not update.message:
        return

    local_id = _parse_local_collection_id(collection_id)
    if local_id is not None:
        results = await search_local_collection_documents(local_id, query)
        if not results:
            await update.message.reply_text("🔎 Brak wyników w lokalnej kolekcji.")
            return
        lines = [f"🔎 <b>Wyniki</b> dla: <i>{escape_html(query)}</i>", ""]
        for idx, row in enumerate(results, start=1):
            snippet = str(row.get("snippet", "")).strip() or "(brak podglądu)"
            lines.append(
                f"{idx}. <b>{escape_html(str(row.get('filename', 'plik')))}</b>\n"
                f"{escape_html(snippet[:280])}"
            )
        await update.message.reply_text("\n\n".join(lines), parse_mode="HTML")
        return

    tool = {
        "type": "function",
        "function": {
            "name": "collections_search",
            "parameters": {"collection_ids": [collection_id]},
        },
    }
    messages = [
        {
            "role": "system",
            "content": "Odpowiedz na pytanie użytkownika używając danych z wskazanej kolekcji.",
        },
        {"role": "user", "content": query},
    ]
    try:
        response = await grok.chat(
            messages=messages,
            model=settings.xai_model_reasoning,
            max_tokens=settings.max_output_tokens,
            reasoning_effort="medium",
            tools=[tool],
        )
    except Exception:
        logger.exception("remote_collection_search_failed", collection_id=collection_id)
        await update.message.reply_text("❌ Wyszukiwanie w xAI Collections nie powiodło się.")
        return

    choices = response.get("choices", [])
    message = choices[0].get("message", {}) if choices else {}
    content = str(message.get("content") or "").strip()
    if not content:
        await update.message.reply_text("🔎 Brak odpowiedzi z wyszukiwania kolekcji.")
        return
    await update.message.reply_text(content)


async def collection_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /collection command and subcommands."""
    if not update.effective_user or not update.message:
        return
    if not await check_access(update, settings):
        return

    grok: GrokClient | None = context.application.bot_data.get("grok_client")
    if grok is None:
        await update.message.reply_text("❌ Klient Grok nie został zainicjalizowany.")
        return

    if not context.args:
        await _show_menu(update, grok)
        return

    action = context.args[0].lower().strip()
    args = context.args[1:]

    if action == "create":
        name = " ".join(args).strip()
        if not name:
            await update.message.reply_text("Użycie: /collection create <nazwa>")
            return
        await _create_collection(update, grok, name)
        return

    if action == "add":
        if not args:
            await update.message.reply_text("Użycie: /collection add <id> (reply na plik)")
            return
        await _add_document(update, context, grok, args[0].strip())
        return

    if action == "search":
        if len(args) < 2:
            await update.message.reply_text("Użycie: /collection search <id> <query>")
            return
        collection_id = args[0].strip()
        query = " ".join(args[1:]).strip()
        await _search_collection(update, grok, collection_id, query)
        return

    if action == "list":
        if not args:
            await update.message.reply_text("Użycie: /collection list <id>")
            return
        await _list_documents(update, grok, args[0].strip())
        return

    if action == "delete":
        if not args:
            await update.message.reply_text("Użycie: /collection delete <id>")
            return
        await _delete_collection(update, grok, args[0].strip())
        return

    await update.message.reply_text(
        "Nieznana akcja. Użyj: create, add, search, list, delete."
    )
