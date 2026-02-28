"""Async SQLite database layer for GigaGrok Bot."""

from __future__ import annotations

from datetime import date as date_type, datetime, timezone
from typing import Any

import aiosqlite
import structlog

from config import settings

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Cost constants (xAI pricing as of 2026‑02)
# ---------------------------------------------------------------------------
COST_PER_M_INPUT: float = 0.20   # $0.20 per 1M input tokens
COST_PER_M_OUTPUT: float = 0.50  # $0.50 per 1M output tokens (reasoning too)
FTS_SNIPPET_TOKENS: int = 18


def calculate_cost(tokens_in: int, tokens_out: int, reasoning_tokens: int) -> float:
    """Return estimated USD cost for a single request."""
    input_cost = (tokens_in / 1_000_000) * COST_PER_M_INPUT
    output_cost = ((tokens_out + reasoning_tokens) / 1_000_000) * COST_PER_M_OUTPUT
    return round(input_cost + output_cost, 6)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    reasoning_content TEXT,
    model TEXT,
    tokens_in INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    reasoning_tokens INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_settings (
    user_id INTEGER PRIMARY KEY,
    system_prompt TEXT,
    reasoning_effort TEXT DEFAULT 'high',
    voice_enabled INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS usage_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    total_requests INTEGER DEFAULT 0,
    total_tokens_in INTEGER DEFAULT 0,
    total_tokens_out INTEGER DEFAULT 0,
    total_reasoning_tokens INTEGER DEFAULT 0,
    total_cost_usd REAL DEFAULT 0.0,
    UNIQUE(user_id, date)
);

CREATE TABLE IF NOT EXISTS dynamic_users (
    user_id INTEGER PRIMARY KEY,
    added_by INTEGER NOT NULL,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS local_collections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS local_collection_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collection_id INTEGER NOT NULL,
    filename TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(collection_id, filename),
    FOREIGN KEY(collection_id) REFERENCES local_collections(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_conv_user_time ON conversations(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_stats_user_date ON usage_stats(user_id, date);
CREATE INDEX IF NOT EXISTS idx_local_docs_collection ON local_collection_documents(collection_id, created_at DESC);
"""

_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS local_collection_documents_fts
USING fts5(filename, content, content='local_collection_documents', content_rowid='id');

CREATE TRIGGER IF NOT EXISTS local_docs_ai AFTER INSERT ON local_collection_documents BEGIN
    INSERT INTO local_collection_documents_fts(rowid, filename, content)
    VALUES (new.id, new.filename, new.content);
END;

CREATE TRIGGER IF NOT EXISTS local_docs_ad AFTER DELETE ON local_collection_documents BEGIN
    INSERT INTO local_collection_documents_fts(local_collection_documents_fts, rowid, filename, content)
    VALUES('delete', old.id, old.filename, old.content);
END;

CREATE TRIGGER IF NOT EXISTS local_docs_au AFTER UPDATE ON local_collection_documents BEGIN
    INSERT INTO local_collection_documents_fts(local_collection_documents_fts, rowid, filename, content)
    VALUES('delete', old.id, old.filename, old.content);
    INSERT INTO local_collection_documents_fts(rowid, filename, content)
    VALUES (new.id, new.filename, new.content);
END;
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


async def _get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(settings.db_path)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    return db


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def init_db() -> None:
    """Create tables if they don't exist."""
    db = await _get_db()
    try:
        await db.executescript(_SCHEMA)
        try:
            await db.executescript(_FTS_SCHEMA)
        except Exception:
            logger.warning("fts5_init_failed_fallback_like_enabled")
        await db.commit()
        logger.info("database_initialized", path=settings.db_path)
    finally:
        await db.close()


async def save_message(
    user_id: int,
    role: str,
    content: str,
    reasoning_content: str | None = None,
    model: str | None = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    reasoning_tokens: int = 0,
    cost_usd: float = 0.0,
) -> None:
    """Persist a single conversation message."""
    db = await _get_db()
    try:
        await db.execute(
            """
            INSERT INTO conversations
                (user_id, role, content, reasoning_content, model,
                 tokens_in, tokens_out, reasoning_tokens, cost_usd)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, role, content, reasoning_content, model,
             tokens_in, tokens_out, reasoning_tokens, cost_usd),
        )
        await db.commit()
    except Exception:
        logger.exception("save_message_failed", user_id=user_id, role=role)
    finally:
        await db.close()


async def get_history(user_id: int, limit: int = 20) -> list[dict[str, str]]:
    """Return the last *limit* messages for *user_id* (oldest first)."""
    db = await _get_db()
    try:
        cursor = await db.execute(
            """
            SELECT role, content FROM (
                SELECT role, content, created_at
                FROM conversations
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT ?
            ) sub ORDER BY created_at ASC
            """,
            (user_id, limit),
        )
        rows = await cursor.fetchall()
        return [{"role": row["role"], "content": row["content"]} for row in rows]
    except Exception:
        logger.exception("get_history_failed", user_id=user_id)
        return []
    finally:
        await db.close()


async def clear_history(user_id: int) -> int:
    """Delete all conversation rows for *user_id*. Return count deleted."""
    db = await _get_db()
    try:
        cursor = await db.execute(
            "DELETE FROM conversations WHERE user_id = ?", (user_id,)
        )
        await db.commit()
        return cursor.rowcount  # type: ignore[return-value]
    except Exception:
        logger.exception("clear_history_failed", user_id=user_id)
        return 0
    finally:
        await db.close()


async def update_daily_stats(
    user_id: int,
    tokens_in: int,
    tokens_out: int,
    reasoning_tokens: int,
    cost_usd: float,
) -> None:
    """Upsert today's aggregated usage stats."""
    today = _today()
    db = await _get_db()
    try:
        await db.execute(
            """
            INSERT INTO usage_stats
                (user_id, date, total_requests, total_tokens_in,
                 total_tokens_out, total_reasoning_tokens, total_cost_usd)
            VALUES (?, ?, 1, ?, ?, ?, ?)
            ON CONFLICT(user_id, date) DO UPDATE SET
                total_requests = total_requests + 1,
                total_tokens_in = total_tokens_in + excluded.total_tokens_in,
                total_tokens_out = total_tokens_out + excluded.total_tokens_out,
                total_reasoning_tokens = total_reasoning_tokens + excluded.total_reasoning_tokens,
                total_cost_usd = total_cost_usd + excluded.total_cost_usd
            """,
            (user_id, today, tokens_in, tokens_out, reasoning_tokens, cost_usd),
        )
        await db.commit()
    except Exception:
        logger.exception("update_daily_stats_failed", user_id=user_id)
    finally:
        await db.close()


async def get_daily_stats(user_id: int, date: str | None = None) -> dict[str, Any]:
    """Return aggregated stats for *date* (default: today)."""
    target = date or _today()
    db = await _get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM usage_stats WHERE user_id = ? AND date = ?",
            (user_id, target),
        )
        row = await cursor.fetchone()
        if row:
            return dict(row)
        return {
            "user_id": user_id,
            "date": target,
            "total_requests": 0,
            "total_tokens_in": 0,
            "total_tokens_out": 0,
            "total_reasoning_tokens": 0,
            "total_cost_usd": 0.0,
        }
    except Exception:
        logger.exception("get_daily_stats_failed", user_id=user_id)
        return {}
    finally:
        await db.close()


async def get_all_time_stats(user_id: int) -> dict[str, Any]:
    """Return all‑time aggregated stats for *user_id*."""
    db = await _get_db()
    try:
        cursor = await db.execute(
            """
            SELECT
                COALESCE(SUM(total_requests), 0)         AS total_requests,
                COALESCE(SUM(total_tokens_in), 0)        AS total_tokens_in,
                COALESCE(SUM(total_tokens_out), 0)       AS total_tokens_out,
                COALESCE(SUM(total_reasoning_tokens), 0) AS total_reasoning_tokens,
                COALESCE(SUM(total_cost_usd), 0.0)       AS total_cost_usd
            FROM usage_stats
            WHERE user_id = ?
            """,
            (user_id,),
        )
        row = await cursor.fetchone()
        if row:
            return dict(row)
        return {
            "total_requests": 0,
            "total_tokens_in": 0,
            "total_tokens_out": 0,
            "total_reasoning_tokens": 0,
            "total_cost_usd": 0.0,
        }
    except Exception:
        logger.exception("get_all_time_stats_failed", user_id=user_id)
        return {}
    finally:
        await db.close()


_ALLOWED_SETTING_COLUMNS: frozenset[str] = frozenset(
    {"system_prompt", "reasoning_effort", "voice_enabled"}
)


async def set_user_setting(user_id: int, key: str, value: str) -> None:
    """Create or update a single user setting."""
    if key not in _ALLOWED_SETTING_COLUMNS:
        logger.warning("set_user_setting_invalid_key", user_id=user_id, key=key)
        return
    db = await _get_db()
    try:
        # Ensure user row exists
        await db.execute(
            "INSERT OR IGNORE INTO user_settings (user_id) VALUES (?)",
            (user_id,),
        )
        await db.execute(
            f"UPDATE user_settings SET {key} = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",  # key validated above
            (value, user_id),
        )
        await db.commit()
    except Exception:
        logger.exception("set_user_setting_failed", user_id=user_id, key=key)
    finally:
        await db.close()


async def get_user_setting(user_id: int, key: str) -> str | None:
    """Return a single setting value or ``None``."""
    if key not in _ALLOWED_SETTING_COLUMNS:
        logger.warning("get_user_setting_invalid_key", user_id=user_id, key=key)
        return None
    db = await _get_db()
    try:
        cursor = await db.execute(
            f"SELECT {key} FROM user_settings WHERE user_id = ?",  # key validated above
            (user_id,),
        )
        row = await cursor.fetchone()
        if row:
            return row[key]
        return None
    except Exception:
        logger.exception("get_user_setting_failed", user_id=user_id, key=key)
        return None
    finally:
        await db.close()


async def add_dynamic_user(user_id: int, added_by: int) -> bool:
    """Dodaj użytkownika do dynamicznej listy dostępów."""
    db = await _get_db()
    try:
        await db.execute(
            "INSERT OR IGNORE INTO dynamic_users (user_id, added_by) VALUES (?, ?)",
            (user_id, added_by),
        )
        await db.commit()
        return True
    except Exception:
        logger.exception("add_dynamic_user_failed", user_id=user_id, added_by=added_by)
        return False
    finally:
        await db.close()


async def remove_dynamic_user(user_id: int) -> int:
    """Usuń użytkownika z dynamicznej listy dostępów."""
    db = await _get_db()
    try:
        cursor = await db.execute("DELETE FROM dynamic_users WHERE user_id = ?", (user_id,))
        await db.commit()
        return cursor.rowcount  # type: ignore[return-value]
    except Exception:
        logger.exception("remove_dynamic_user_failed", user_id=user_id)
        return 0
    finally:
        await db.close()


async def is_dynamic_user_allowed(user_id: int) -> bool:
    """Sprawdź, czy użytkownik istnieje na dynamicznej liście dostępów."""
    db = await _get_db()
    try:
        cursor = await db.execute(
            "SELECT 1 FROM dynamic_users WHERE user_id = ? LIMIT 1",
            (user_id,),
        )
        row = await cursor.fetchone()
        return row is not None
    except Exception:
        logger.exception("is_dynamic_user_allowed_failed", user_id=user_id)
        return False
    finally:
        await db.close()


async def list_dynamic_users() -> list[int]:
    """Zwróć listę użytkowników dodanych dynamicznie."""
    db = await _get_db()
    try:
        cursor = await db.execute(
            "SELECT user_id FROM dynamic_users ORDER BY added_at ASC, user_id ASC"
        )
        rows = await cursor.fetchall()
        return [int(row["user_id"]) for row in rows]
    except Exception:
        logger.exception("list_dynamic_users_failed")
        return []
    finally:
        await db.close()


async def get_users_usage_summary(
    user_ids: list[int],
) -> dict[int, dict[str, int | float]]:
    """Zwróć zagregowane statystyki usage dla podanych użytkowników."""
    if not user_ids:
        return {}

    placeholders = ",".join("?" for _ in user_ids)
    query = (
        "SELECT user_id, COALESCE(SUM(total_requests), 0) AS total_requests, "
        "COALESCE(SUM(total_cost_usd), 0.0) AS total_cost_usd "
        f"FROM usage_stats WHERE user_id IN ({placeholders}) GROUP BY user_id"
    )

    db = await _get_db()
    try:
        cursor = await db.execute(query, tuple(user_ids))
        rows = await cursor.fetchall()
        result: dict[int, dict[str, int | float]] = {
            int(user_id): {"total_requests": 0, "total_cost_usd": 0.0}
            for user_id in user_ids
        }
        for row in rows:
            uid = int(row["user_id"])
            result[uid] = {
                "total_requests": int(row["total_requests"]),
                "total_cost_usd": float(row["total_cost_usd"]),
            }
        return result
    except Exception:
        logger.exception("get_users_usage_summary_failed", user_ids=user_ids)
        return {
            int(user_id): {"total_requests": 0, "total_cost_usd": 0.0}
            for user_id in user_ids
        }
    finally:
        await db.close()


async def create_local_collection(name: str) -> int | None:
    """Create local fallback collection and return collection ID."""
    db = await _get_db()
    try:
        cursor = await db.execute(
            "INSERT INTO local_collections (name) VALUES (?)",
            (name.strip(),),
        )
        await db.commit()
        row_id = cursor.lastrowid
        return int(row_id) if row_id is not None else None
    except Exception:
        logger.exception("create_local_collection_failed", name=name)
        return None
    finally:
        await db.close()


async def list_local_collections() -> list[dict[str, Any]]:
    """Return local fallback collections with document counts."""
    db = await _get_db()
    try:
        cursor = await db.execute(
            """
            SELECT c.id, c.name, c.created_at, COUNT(d.id) AS document_count
            FROM local_collections c
            LEFT JOIN local_collection_documents d ON d.collection_id = c.id
            GROUP BY c.id, c.name, c.created_at
            ORDER BY c.created_at DESC, c.id DESC
            """
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    except Exception:
        logger.exception("list_local_collections_failed")
        return []
    finally:
        await db.close()


async def delete_local_collection(collection_id: int) -> int:
    """Delete local fallback collection by ID."""
    db = await _get_db()
    try:
        cursor = await db.execute("DELETE FROM local_collections WHERE id = ?", (collection_id,))
        await db.commit()
        return cursor.rowcount  # type: ignore[return-value]
    except Exception:
        logger.exception("delete_local_collection_failed", collection_id=collection_id)
        return 0
    finally:
        await db.close()


async def add_local_collection_document(
    collection_id: int,
    filename: str,
    content: str,
) -> bool:
    """Upsert local fallback document in a collection."""
    db = await _get_db()
    try:
        await db.execute(
            """
            INSERT INTO local_collection_documents (collection_id, filename, content)
            VALUES (?, ?, ?)
            ON CONFLICT(collection_id, filename) DO UPDATE SET
                content = excluded.content,
                created_at = CURRENT_TIMESTAMP
            """,
            (collection_id, filename, content),
        )
        await db.commit()
        return True
    except Exception:
        logger.exception(
            "add_local_collection_document_failed",
            collection_id=collection_id,
            filename=filename,
        )
        return False
    finally:
        await db.close()


async def list_local_collection_documents(collection_id: int) -> list[dict[str, Any]]:
    """List local fallback documents in a collection."""
    db = await _get_db()
    try:
        cursor = await db.execute(
            """
            SELECT id, filename, created_at
            FROM local_collection_documents
            WHERE collection_id = ?
            ORDER BY created_at DESC, id DESC
            """,
            (collection_id,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    except Exception:
        logger.exception("list_local_collection_documents_failed", collection_id=collection_id)
        return []
    finally:
        await db.close()


async def search_local_collection_documents(
    collection_id: int,
    query: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Search local fallback documents with FTS5; fallback to LIKE."""
    db = await _get_db()
    try:
        try:
            cursor = await db.execute(
                """
                SELECT d.id, d.filename, snippet(local_collection_documents_fts, 1, '[', ']', '…', ?) AS snippet
                FROM local_collection_documents_fts
                JOIN local_collection_documents d ON d.id = local_collection_documents_fts.rowid
                WHERE d.collection_id = ? AND local_collection_documents_fts MATCH ?
                ORDER BY bm25(local_collection_documents_fts)
                LIMIT ?
                """,
                (FTS_SNIPPET_TOKENS, collection_id, query, limit),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception:
            # Escape LIKE special characters to prevent wildcard injection
            safe_query = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            cursor = await db.execute(
                """
                SELECT id, filename, substr(content, 1, 220) AS snippet
                FROM local_collection_documents
                WHERE collection_id = ? AND content LIKE ? ESCAPE '\\'
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (collection_id, f"%{safe_query}%", limit),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    except Exception:
        logger.exception(
            "search_local_collection_documents_failed",
            collection_id=collection_id,
            query=query,
        )
        return []
    finally:
        await db.close()
