"""/start and /help command handlers for GigaGrok Bot."""

from __future__ import annotations

import structlog
from telegram import Update
from telegram.ext import ContextTypes

from config import settings
from utils import check_access

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------
_START_TEXT = (
    "🧠 <b>GigaGrok</b> — Twój asystent AI\n"
    "\n"
    "Zasilany przez <b>Grok 4.1 Fast Reasoning</b>\n"
    "• 2M tokenów kontekstu\n"
    "• Deep reasoning (chain-of-thought)\n"
    "• Web search, X search, code execution\n"
    "• Analiza obrazów i dokumentów\n"
    "\n"
    "Wyślij mi wiadomość, a odpowiem z pełną mocą reasoning.\n"
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


# ---------------------------------------------------------------------------
# /help
# ---------------------------------------------------------------------------
_HELP_TEXT = (
    "📚 <b>Komendy GigaGrok</b>\n"
    "\n"
    "💬 <b>Chat:</b>\n"
    "Wyślij wiadomość → odpowiedź z reasoning\n"
    "🎤 Wyślij voice/audio → auto-transkrypcja + odpowiedź Grok\n"
    "\n"
    "⚡ /fast &lt;tekst&gt; → szybka odpowiedź bez reasoning\n"
    "🔍 /websearch &lt;query&gt; → szukaj w internecie\n"
    "🐦 /xsearch &lt;query&gt; → szukaj na X/Twitter\n"
    "🖼 /image &lt;prompt&gt; (odpowiedz na zdjęcie) → analiza obrazu\n"
    "📎 /file &lt;prompt&gt; (odpowiedz na plik) → analiza pliku\n"
    "🚀 /gigagrok &lt;prompt&gt; → FULL POWER mode\n"
    "\n"
    "⚙️ <b>Ustawienia:</b>\n"
    "/voice → toggle odpowiedzi głosowych\n"
    "\n"
    "💡 Wskazówka: zwykłe wysłanie zdjęcia lub dokumentu uruchamia analizę automatycznie.\n"
    "\n"
    "📦 /collection → zarządzaj bazą wiedzy\n"
    "\n"
    "🐙 /github &lt;plik&gt; → odczytaj plik z workspace i dodaj do następnego pytania\n"
    "🛠 /workspace write &lt;plik&gt; → zapisz treść z wiadomości-reply do pliku"
)

_HELP_ADMIN_SECTION = (
    "\n\n"
    "👑 <b>Admin:</b>\n"
    "/users → lista dozwolonych użytkowników\n"
    "/adduser &lt;id&gt; → dodaj użytkownika\n"
    "/removeuser &lt;id&gt; → usuń użytkownika"
)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /help command."""
    if not update.effective_user or not update.message:
        return

    if not await check_access(update, settings):
        return
    user_id = update.effective_user.id

    logger.info("help_command", user_id=user_id)
    text = _HELP_TEXT
    if settings.is_admin(user_id):
        text += _HELP_ADMIN_SECTION
    await update.message.reply_text(text, parse_mode="HTML")
