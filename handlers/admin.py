"""Admin command handlers for dynamic user management."""

from __future__ import annotations

import structlog
from telegram import Update
from telegram.ext import ContextTypes

from config import settings
from db import (
    add_dynamic_user,
    get_users_usage_summary,
    is_dynamic_user_allowed,
    list_dynamic_users,
    remove_dynamic_user,
)
from utils import check_access

logger = structlog.get_logger(__name__)


def _parse_user_id(arg: str | None) -> int | None:
    """Parse a user ID argument safely."""
    if not arg:
        return None
    value = arg.strip()
    if not value.isdigit():
        return None
    return int(value)


async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /users - list all allowed users with basic usage stats."""
    del context
    if not update.effective_user or not update.message:
        return
    if not await check_access(update, settings):
        return

    user_id = update.effective_user.id
    if not settings.is_admin(user_id):
        await update.message.reply_text("⛔ Ta komenda jest tylko dla admina.")
        return

    static_users = settings.allowed_users
    dynamic_users = await list_dynamic_users()
    all_users = sorted(static_users.union(set(dynamic_users)))
    if not all_users:
        await update.message.reply_text("👥 Brak dozwolonych użytkowników.")
        return

    stats = await get_users_usage_summary(all_users)
    lines: list[str] = ["👥 <b>Dozwoleni użytkownicy:</b>", ""]
    for idx, allowed_user_id in enumerate(all_users, start=1):
        user_stats = stats.get(
            allowed_user_id, {"total_requests": 0.0, "total_cost_usd": 0.0}
        )
        total_requests = int(user_stats.get("total_requests", 0.0))
        total_cost = float(user_stats.get("total_cost_usd", 0.0))
        is_admin = settings.is_admin(allowed_user_id)
        icon = "👑" if is_admin else "👤"
        admin_suffix = " (admin)" if is_admin else ""
        if total_requests > 0:
            lines.append(
                f"{idx}. {icon} <code>{allowed_user_id}</code>{admin_suffix} — "
                f"{total_requests} wiadomości, ${total_cost:.3f}"
            )
        else:
            lines.append(
                f"{idx}. {icon} <code>{allowed_user_id}</code>{admin_suffix} — 0 wiadomości"
            )
    lines.append("")
    lines.append(f"Łącznie: {len(all_users)} użytkowników")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def adduser_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /adduser <id> - add dynamic allowed user (admin only)."""
    if not update.effective_user or not update.message:
        return
    if not await check_access(update, settings):
        return

    admin_user_id = update.effective_user.id
    if not settings.is_admin(admin_user_id):
        await update.message.reply_text("⛔ Ta komenda jest tylko dla admina.")
        return

    target_user_id = _parse_user_id(context.args[0] if context.args else None)
    if target_user_id is None:
        await update.message.reply_text("Użycie: /adduser <telegram_user_id>")
        return

    if target_user_id in settings.allowed_users or await is_dynamic_user_allowed(
        target_user_id
    ):
        await update.message.reply_text(f"ℹ️ Użytkownik {target_user_id} już ma dostęp.")
        return

    ok = await add_dynamic_user(target_user_id, admin_user_id)
    if not ok:
        await update.message.reply_text("❌ Nie udało się dodać użytkownika.")
        return

    logger.info("user_added", admin_user_id=admin_user_id, target_user_id=target_user_id)
    await update.message.reply_text(
        f"✅ Dodano użytkownika <code>{target_user_id}</code>.",
        parse_mode="HTML",
    )


async def removeuser_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /removeuser <id> - remove dynamic allowed user (admin only)."""
    if not update.effective_user or not update.message:
        return
    if not await check_access(update, settings):
        return

    admin_user_id = update.effective_user.id
    if not settings.is_admin(admin_user_id):
        await update.message.reply_text("⛔ Ta komenda jest tylko dla admina.")
        return

    target_user_id = _parse_user_id(context.args[0] if context.args else None)
    if target_user_id is None:
        await update.message.reply_text("Użycie: /removeuser <telegram_user_id>")
        return

    if target_user_id in settings.allowed_users:
        await update.message.reply_text(
            "⚠️ Tego użytkownika nie można usunąć komendą, bo jest w ALLOWED_USER_IDS/ADMIN_USER_ID."
        )
        return

    removed = await remove_dynamic_user(target_user_id)
    if removed <= 0:
        await update.message.reply_text(f"ℹ️ Użytkownik {target_user_id} nie był na liście.")
        return

    logger.info(
        "user_removed",
        admin_user_id=admin_user_id,
        target_user_id=target_user_id,
    )
    await update.message.reply_text(
        f"✅ Usunięto użytkownika <code>{target_user_id}</code>.",
        parse_mode="HTML",
    )
