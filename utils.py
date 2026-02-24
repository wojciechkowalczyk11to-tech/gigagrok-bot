"""Utility / helper functions for GigaGrok Bot."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Protocol

from telegram import Update


class AccessSettings(Protocol):
    """Minimal settings interface required for access checks."""

    def is_allowed(self, user_id: int) -> bool:
        """Return True if user is allowed."""


# ---------------------------------------------------------------------------
# HTML escaping (Telegram HTML parse mode)
# ---------------------------------------------------------------------------
def escape_html(text: str) -> str:
    """Escape ``<``, ``>``, and ``&`` for Telegram HTML parse mode."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------------------
# Message splitting
# ---------------------------------------------------------------------------
_CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```")


def split_message(text: str, max_length: int = 4000) -> list[str]:
    """Split *text* into chunks that fit Telegram's 4096‑char limit.

    Splitting priority: double‑newline → newline → space.
    Code blocks (````` … `````) are never cut in the middle.
    """
    if len(text) <= max_length:
        return [text]

    parts: list[str] = []
    remaining = text

    while remaining:
        if len(remaining) <= max_length:
            parts.append(remaining)
            break

        chunk = remaining[:max_length]

        # Don't split inside a code block
        open_ticks = chunk.count("```")
        if open_ticks % 2 != 0:
            # We're inside a code block — find its start and cut before it
            last_open = chunk.rfind("```")
            if last_open > 0:
                chunk = chunk[:last_open]

        # Try to split at a natural boundary
        split_pos = _find_split_pos(chunk)
        chunk = remaining[:split_pos]
        remaining = remaining[split_pos:].lstrip("\n")
        parts.append(chunk)

    return parts if parts else [text]


def _find_split_pos(chunk: str) -> int:
    """Return the best split position inside *chunk*."""
    # 1. Double newline
    pos = chunk.rfind("\n\n")
    if pos > 0:
        return pos

    # 2. Single newline
    pos = chunk.rfind("\n")
    if pos > 0:
        return pos

    # 3. Space
    pos = chunk.rfind(" ")
    if pos > 0:
        return pos

    # 4. Hard cut
    return len(chunk)


# ---------------------------------------------------------------------------
# Footer formatting
# ---------------------------------------------------------------------------
def format_footer(
    model: str,
    tokens_in: int,
    tokens_out: int,
    reasoning_tokens: int,
    cost_usd: float,
    elapsed_seconds: float,
) -> str:
    """Return a compact one‑line footer for a bot response."""
    return (
        f"⚙️ {model} | "
        f"📥 {format_number(tokens_in)} "
        f"📤 {format_number(tokens_out)} "
        f"🧠 {format_number(reasoning_tokens)} | "
        f"💰 ${cost_usd:.4f} | "
        f"⏱ {elapsed_seconds:.1f}s"
    )


def format_gigagrok_footer(
    model: str,
    tokens_in: int,
    tokens_out: int,
    reasoning_tokens: int,
    cost_usd: float,
    elapsed_seconds: float,
    tools_used: list[str],
) -> str:
    """Return extended footer for /gigagrok responses."""
    tools_label = ", ".join(tools_used) if tools_used else "brak"
    return (
        f"🚀 GIGAGROK | {model} | "
        f"📥 {format_number(tokens_in)} "
        f"📤 {format_number(tokens_out)} "
        f"🧠 {format_number(reasoning_tokens)} | "
        f"🔧 {tools_label} | "
        f"💰 ${cost_usd:.4f} | "
        f"⏱ {elapsed_seconds:.1f}s"
    )


# ---------------------------------------------------------------------------
# Number formatting
# ---------------------------------------------------------------------------
def format_number(n: int) -> str:
    """Format large numbers with K/M suffixes (e.g. 1234 → ``1.2K``)."""
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if abs(n) >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


# ---------------------------------------------------------------------------
# Date
# ---------------------------------------------------------------------------
def get_current_date() -> str:
    """Return today's date as ``YYYY-MM-DD`` (UTC)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


async def check_access(update: Update, app_settings: AccessSettings) -> bool:
    """Sprawdź dostęp użytkownika; zwróć True jeśli ma uprawnienia."""
    if not update.effective_user or not update.message:
        return False

    user_id = update.effective_user.id
    is_allowed = app_settings.is_allowed(user_id)
    if not is_allowed:
        from db import is_dynamic_user_allowed

        is_allowed = await is_dynamic_user_allowed(user_id)

    if not is_allowed:
        await update.message.reply_text(
            f"⛔ Brak dostępu.\nTwoje ID: <code>{user_id}</code>\nPoproś admina o dodanie.",
            parse_mode="HTML",
        )
        return False
    return True
