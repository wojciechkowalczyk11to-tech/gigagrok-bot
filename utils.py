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
# Markdown → Telegram HTML conversion
# ---------------------------------------------------------------------------
_CODE_BLOCK_CONVERT_RE = re.compile(r"```(\w*)\n([\s\S]*?)```")
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^\*]+)\*(?!\*)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\)]+)\)")


def markdown_to_telegram_html(text: str) -> str:
    """Convert common Markdown to Telegram-safe HTML.

    Handles code blocks, inline code, bold, italic, and links.
    All content is HTML-escaped to prevent broken tags in Telegram.
    """
    # 1. Extract code blocks first to protect their contents
    code_blocks: list[str] = []

    def _replace_code_block(m: re.Match[str]) -> str:
        idx = len(code_blocks)
        escaped_code = escape_html(m.group(2).rstrip("\n"))
        code_blocks.append(f"<pre><code>{escaped_code}</code></pre>")
        return f"\x00CODEBLOCK{idx}\x00"

    result = _CODE_BLOCK_CONVERT_RE.sub(_replace_code_block, text)

    # 2. Extract inline code
    inline_codes: list[str] = []

    def _replace_inline_code(m: re.Match[str]) -> str:
        idx = len(inline_codes)
        inline_codes.append(f"<code>{escape_html(m.group(1))}</code>")
        return f"\x00INLINE{idx}\x00"

    result = _INLINE_CODE_RE.sub(_replace_inline_code, result)

    # 3. Escape HTML in remaining text
    result = escape_html(result)

    # 4. Apply formatting
    result = _BOLD_RE.sub(r"<b>\1</b>", result)
    result = _ITALIC_RE.sub(r"<i>\1</i>", result)
    result = _LINK_RE.sub(r'<a href="\2">\1</a>', result)

    # 5. Restore code blocks and inline codes
    for idx, block in enumerate(code_blocks):
        result = result.replace(f"\x00CODEBLOCK{idx}\x00", block)
    for idx, code in enumerate(inline_codes):
        result = result.replace(f"\x00INLINE{idx}\x00", code)

    return result


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
