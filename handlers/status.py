"""Status command handler — diagnostics for model router, rate limiter, fallback."""

from __future__ import annotations

import structlog
from telegram import Update
from telegram.ext import ContextTypes

from config import settings
from fallback import FallbackManager
from model_router import ModelRouter
from rate_limiter import RateLimiter
from utils import check_access, escape_html

logger = structlog.get_logger(__name__)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show bot infrastructure status: /status"""
    if not update.effective_user or not update.message:
        return
    if not await check_access(update, settings):
        return
    user_id = update.effective_user.id

    lines: list[str] = ["<b>📊 GigaGrok Status</b>\n"]

    # --- Model Router ---
    router: ModelRouter | None = context.bot_data.get("model_router")
    if router:
        status = router.status()
        lines.append("<b>🔀 Model Router</b>")
        for provider, info in status.items():
            avail = "✅" if info["available"] and not info["circuit_open"] else "❌"
            lines.append(f"  {avail} <b>{provider}</b>")
            for profile, model in info["models"].items():
                lines.append(f"    • {profile}: <code>{model}</code>")
            if info["circuit_open"]:
                lines.append("    ⚠️ Circuit breaker OPEN")
        lines.append("")

    # --- Fallback ---
    fallback: FallbackManager | None = context.bot_data.get("fallback_manager")
    if fallback:
        status = fallback.status()
        level_emoji = {
            "full": "🟢", "limited": "🟡", "degraded": "🟠", "minimal": "🔴"
        }
        emoji = level_emoji.get(status["level"], "⚪")
        lines.append(f"<b>🛡️ Degradation</b>: {emoji} {status['level']}")
        lines.append(f"  Recent failures: {status['recent_failures']}")
        lines.append("")

    # --- Rate Limiter ---
    limiter: RateLimiter | None = context.bot_data.get("rate_limiter")
    if limiter:
        remaining = limiter.get_user_remaining(user_id)
        lines.append("<b>⏱️ Your Quota</b>")
        lines.append(f"  Requests: {remaining['requests']} left")
        lines.append(f"  Tokens: {remaining['tokens']:,} left")
        lines.append(f"  Budget: ${remaining['cost_usd']:.2f} left")
        lines.append("")

    # --- Config ---
    lines.append("<b>⚙️ Config</b>")
    lines.append(f"  Mode: <code>{settings.run_mode}</code>")
    lines.append(f"  Multi-model: {'✅' if settings.multi_model_enabled else '❌'}")
    lines.append(f"  Default: <code>{settings.xai_model_reasoning}</code>")
    lines.append(f"  RPM: {settings.rate_limit_rpm}")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")
