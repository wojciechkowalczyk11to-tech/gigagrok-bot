"""GigaGrok Bot — entry point (webhook mode)."""

from __future__ import annotations

import logging
import signal
import traceback

import structlog
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from config import settings
from db import init_db
from grok_client import GrokClient
from healthcheck import start_healthcheck_server
from handlers.admin import adduser_command, removeuser_command, users_command
from handlers.chat import handle_message, init_grok_client
from handlers.collection import collection_command
from handlers.file import file_command, handle_document
from handlers.gigagrok import gigagrok_command
from handlers.github import github_command, workspace_command
from handlers.image import handle_photo, image_command
from handlers.mode import fast_command
from handlers.search import websearch_command, xsearch_command
from handlers.start import help_command, start_command
from handlers.voice import handle_voice, voice_toggle

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(
        getattr(logging, settings.log_level.upper(), logging.INFO)
    ),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger("main")


# ---------------------------------------------------------------------------
# Application lifecycle callbacks
# ---------------------------------------------------------------------------
async def post_init(application: Application) -> None:  # type: ignore[type-arg]
    """Called after the Application is initialised."""
    await init_db()

    grok = GrokClient(api_key=settings.xai_api_key, base_url=settings.xai_base_url)
    init_grok_client(grok)
    application.bot_data["grok_client"] = grok

    logger.info(
        "bot_started",
        model=settings.xai_model_reasoning,
        webhook=f"{settings.webhook_url}/{settings.webhook_path}",
    )


async def post_shutdown(application: Application) -> None:  # type: ignore[type-arg]
    """Called when the Application shuts down."""
    grok: GrokClient | None = application.bot_data.get("grok_client")
    if grok:
        await grok.close()
    logger.info("bot_shutdown")


# ---------------------------------------------------------------------------
# Global error handler
# ---------------------------------------------------------------------------
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Łap wszystkie nieobsłużone wyjątki — user widzi ładny komunikat, nie traceback."""
    logger.error(
        "unhandled_exception",
        error=str(context.error),
        traceback=traceback.format_exception(
            type(context.error), context.error, context.error.__traceback__
        )
        if context.error
        else [],
    )
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "❌ Wystąpił nieoczekiwany błąd. Spróbuj ponownie."
            )
        except Exception:
            logger.exception("error_handler_reply_failed")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    """Build and run the Telegram bot in webhook mode."""
    health_server = None
    try:
        health_server = start_healthcheck_server(settings.db_path, port=8080)
        logger.info("healthcheck_started", port=8080)
    except Exception:
        logger.exception("healthcheck_start_failed")

    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # Global error handler
    app.add_error_handler(error_handler)

    # Handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("fast", fast_command))
    app.add_handler(CommandHandler("websearch", websearch_command))
    app.add_handler(CommandHandler("xsearch", xsearch_command))
    app.add_handler(CommandHandler("image", image_command))
    app.add_handler(CommandHandler("file", file_command))
    app.add_handler(CommandHandler("users", users_command))
    app.add_handler(CommandHandler("adduser", adduser_command))
    app.add_handler(CommandHandler("removeuser", removeuser_command))
    app.add_handler(CommandHandler("collection", collection_command))
    app.add_handler(CommandHandler("gigagrok", gigagrok_command))
    app.add_handler(CommandHandler("github", github_command))
    app.add_handler(CommandHandler("workspace", workspace_command))
    app.add_handler(CommandHandler("voice", voice_toggle))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Webhook
    webhook_url = f"{settings.webhook_url}/{settings.webhook_path}"
    logger.info("starting_webhook", url=webhook_url, port=settings.webhook_port)

    # Graceful shutdown on SIGTERM (e.g. systemd stop)
    def _sigterm_handler(signum: int, frame: object) -> None:
        logger.info("sigterm_received")
        app.stop_running()

    signal.signal(signal.SIGTERM, _sigterm_handler)

    try:
        app.run_webhook(
            listen="0.0.0.0",
            port=settings.webhook_port,
            url_path=settings.webhook_path,
            webhook_url=webhook_url,
            secret_token=settings.webhook_secret,
            allowed_updates=["message", "callback_query"],
        )
    finally:
        if health_server:
            health_server.shutdown()
            health_server.server_close()
            logger.info("healthcheck_stopped")


if __name__ == "__main__":
    main()
