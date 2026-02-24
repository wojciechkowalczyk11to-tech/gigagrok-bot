"""GigaGrok Bot — entry point (webhook mode)."""

from __future__ import annotations

import logging
import sys

import structlog
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from config import settings
from db import init_db
from grok_client import GrokClient
from handlers.admin import adduser_command, removeuser_command, users_command
from handlers.chat import handle_message, init_grok_client
from handlers.file import file_command, handle_document
from handlers.image import handle_photo, image_command
from handlers.mode import fast_command
from handlers.search import websearch_command, xsearch_command
from handlers.start import help_command, start_command

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
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    """Build and run the Telegram bot in webhook mode."""
    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

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
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Webhook
    webhook_url = f"{settings.webhook_url}/{settings.webhook_path}"
    logger.info("starting_webhook", url=webhook_url, port=settings.webhook_port)

    app.run_webhook(
        listen="0.0.0.0",
        port=settings.webhook_port,
        url_path=settings.webhook_path,
        webhook_url=webhook_url,
        secret_token=settings.webhook_secret,
        allowed_updates=["message", "callback_query"],
    )


if __name__ == "__main__":
    main()
