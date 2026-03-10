"""/start and /help command handlers for GigaGrok Bot."""

from __future__ import annotations

import structlog
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config import settings
from utils import check_access

logger = structlog.get_logger(__name__)

_START_TEXT = (
    "🧠 <b>GigaGrok</b> — Twój asystent AI\n"
    "\n"
    "Zasilany przez <b>Grok 4.20 beta</b> (model eksperymentalny)\n"
    "• Domyślny tryb reasoning + szybki tryb non-reasoning\n"
    "• Wbudowane narzędzia: web search i X search\n"
    "• Analiza obrazów i dokumentów\n"
    "\n"
    "Wyślij wiadomość — odpowiem modelem reasoning i podam zwięzłe wnioski.\n"
    "\n"
    "Wpisz /help po listę komend."
)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /start command."""
    if not update.effective_user or not update.message:
        return

    if not await check_access(update, settings):
        return
    user_id = update.effective_user.id

    logger.info("start_command", user_id=user_id)
    await update.message.reply_text(_START_TEXT, parse_mode="HTML")


_HELP_TEXT = (
    "📚 <b>Komendy GigaGrok</b>\n"
    "\n"
    "Kliknij przycisk poniżej, by zobaczyć opis komendy.\n"
    "Lub wpisz komendę bezpośrednio."
)

_HELP_ADMIN_SECTION = (
    "\n\n"
    "👑 <b>Admin:</b>\n"
    "/users → lista dozwolonych użytkowników\n"
    "/adduser &lt;id&gt; → dodaj użytkownika\n"
    "/removeuser &lt;id&gt; → usuń użytkownika"
)

_HELP_KEYBOARD = InlineKeyboardMarkup(
    [
        [
            InlineKeyboardButton("💬 Chat", callback_data="help_chat"),
            InlineKeyboardButton("⚡ Fast", callback_data="help_fast"),
            InlineKeyboardButton("🧠 Think", callback_data="help_think"),
        ],
        [
            InlineKeyboardButton("🔍 Web", callback_data="help_web"),
            InlineKeyboardButton("🐦 X", callback_data="help_x"),
            InlineKeyboardButton("🚀 GigaGrok", callback_data="help_gigagrok"),
        ],
        [
            InlineKeyboardButton("📎 File", callback_data="help_file"),
            InlineKeyboardButton("🎤 Voice", callback_data="help_voice"),
            InlineKeyboardButton("⚙️ Settings", callback_data="help_settings"),
        ],
        [
            InlineKeyboardButton("📊 Stats", callback_data="help_stats"),
            InlineKeyboardButton("🗑 Clear", callback_data="help_clear"),
        ],
    ]
)

_HELP_DESCRIPTIONS: dict[str, str] = {
    "help_chat": (
        "💬 <b>Chat</b>\n\n"
        "Wyślij dowolną wiadomość tekstową, a bot odpowie domyślnym modelem reasoning Grok 4.20 beta.\n\n"
        "Przykład: po prostu napisz pytanie."
    ),
    "help_fast": (
        "⚡ <b>/fast</b>\n\n"
        "Szybka odpowiedź trybem non-reasoning (Grok 4.20 beta).\n\n"
        "Przykład: <code>/fast Co to jest Python?</code>"
    ),
    "help_think": (
        "🧠 <b>/think</b>\n\n"
        "Tryb głębokiego myślenia z dodatkowymi zasadami jakości.\n\n"
        "Przykład: <code>/think Porównaj React vs Vue w 2026</code>"
    ),
    "help_web": (
        "🔍 <b>/websearch</b>\n\n"
        "Przeszukaj internet i podaj aktualne informacje.\n\n"
        "Przykład: <code>/websearch najnowsze wiadomości AI</code>"
    ),
    "help_x": (
        "🐦 <b>/xsearch</b>\n\n"
        "Przeszukaj X/Twitter po najnowsze posty i dyskusje.\n\n"
        "Przykład: <code>/xsearch OpenAI GPT-5</code>"
    ),
    "help_gigagrok": (
        "🚀 <b>/gigagrok</b>\n\n"
        "Tryb rozszerzony: historia + web/X + analiza kodu.\n"
        "Automatycznie dobiera narzędzia, gdy to pomaga w odpowiedzi.\n\n"
        "Przykład: <code>/gigagrok Przeanalizuj trend AI w Polsce</code>"
    ),
    "help_file": (
        "📎 <b>/file</b>\n\n"
        "Odpowiedz na plik z promptem do analizy.\n"
        "Obsługuje: PDF, DOCX, TXT, ZIP.\n\n"
        "Przykład: wyślij plik, odpowiedz <code>/file podsumuj</code>"
    ),
    "help_voice": (
        "🎤 <b>Voice</b>\n\n"
        "Wyślij wiadomość głosową — auto transkrypcja + odpowiedź Grok.\n"
        "/voice — toggle odpowiedzi głosowych.\n\n"
        "Przykład: nagraj voice message."
    ),
    "help_settings": (
        "⚙️ <b>Ustawienia</b>\n\n"
        "/system — pokaż/ustaw system prompt\n"
        "/system reset — resetuj do domyślnego\n"
        "/profile list — lista profili osobowości\n"
        "/profile &lt;nazwa&gt; — aktywuj profil"
    ),
    "help_stats": (
        "📊 <b>/stats</b>\n\n"
        "Pokaż statystyki użycia: tokeny, koszt, zapytania.\n\n"
        "Przykład: <code>/stats</code>"
    ),
    "help_clear": (
        "🗑 <b>/clear</b>\n\n"
        "Wyczyść historię konwersacji.\n\n"
        "Przykład: <code>/clear</code>"
    ),
}


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /help command with inline keyboard."""
    if not update.effective_user or not update.message:
        return

    if not await check_access(update, settings):
        return
    user_id = update.effective_user.id

    logger.info("help_command", user_id=user_id)
    text = _HELP_TEXT
    if settings.is_admin(user_id):
        text += _HELP_ADMIN_SECTION
    await update.message.reply_text(
        text, parse_mode="HTML", reply_markup=_HELP_KEYBOARD
    )


async def help_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle callback queries from /help inline keyboard."""
    query = update.callback_query
    if not query or not query.data:
        return

    await query.answer()

    description = _HELP_DESCRIPTIONS.get(query.data, "❓ Nieznana komenda.")
    try:
        await query.edit_message_text(
            text=description,
            parse_mode="HTML",
            reply_markup=_HELP_KEYBOARD,
        )
    except Exception:
        pass
