"""Tests for db module with in-memory SQLite."""

from __future__ import annotations

import pytest
import pytest_asyncio

import aiosqlite

import db as db_module
from db import (
    _SCHEMA,
    add_dynamic_user,
    calculate_cost,
    clear_history,
    get_daily_stats,
    get_history,
    get_user_setting,
    is_dynamic_user_allowed,
    remove_dynamic_user,
    save_message,
    set_user_setting,
    update_daily_stats,
)


@pytest_asyncio.fixture(autouse=True)
async def reset_db():
    """Set up a fresh in-memory SQLite database for each test."""
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    await conn.executescript(_SCHEMA)
    await conn.commit()
    db_module._db = conn
    yield
    await conn.close()
    db_module._db = None


# ---------------------------------------------------------------------------
# init_db
# ---------------------------------------------------------------------------

class TestInitDb:
    @pytest.mark.asyncio
    async def test_init_db_creates_tables(self) -> None:
        # The autouse fixture already ran _SCHEMA; verify tables exist
        db = db_module._db
        assert db is not None
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='conversations'"
        )
        row = await cursor.fetchone()
        assert row is not None


# ---------------------------------------------------------------------------
# save_message / get_history / clear_history
# ---------------------------------------------------------------------------

class TestHistory:
    @pytest.mark.asyncio
    async def test_save_and_get_history(self) -> None:
        await save_message(user_id=1, role="user", content="hello")
        await save_message(user_id=1, role="assistant", content="hi there")

        history = await get_history(user_id=1)
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[0]["content"] == "hello"
        assert history[1]["role"] == "assistant"
        assert history[1]["content"] == "hi there"

    @pytest.mark.asyncio
    async def test_clear_history(self) -> None:
        await save_message(user_id=1, role="user", content="msg1")
        await save_message(user_id=2, role="user", content="msg2")

        deleted = await clear_history(user_id=1)
        assert deleted == 1

        # User 1 should have no history
        assert await get_history(user_id=1) == []
        # User 2 should still have history
        assert len(await get_history(user_id=2)) == 1


# ---------------------------------------------------------------------------
# calculate_cost
# ---------------------------------------------------------------------------

class TestCalculateCost:
    def test_calculate_cost(self) -> None:
        cost = calculate_cost(tokens_in=1_000_000, tokens_out=500_000, reasoning_tokens=500_000)
        # input: 1M * 0.20 = 0.20
        # output: (500k + 500k) / 1M * 0.50 = 0.50
        assert cost == pytest.approx(0.70, abs=1e-6)

    def test_calculate_cost_zero(self) -> None:
        assert calculate_cost(0, 0, 0) == 0.0


# ---------------------------------------------------------------------------
# update_daily_stats / get_daily_stats
# ---------------------------------------------------------------------------

class TestDailyStats:
    @pytest.mark.asyncio
    async def test_update_and_get_daily_stats(self) -> None:
        await update_daily_stats(
            user_id=1,
            tokens_in=100,
            tokens_out=200,
            reasoning_tokens=50,
            cost_usd=0.01,
        )
        # Second call — upsert should increment
        await update_daily_stats(
            user_id=1,
            tokens_in=300,
            tokens_out=400,
            reasoning_tokens=100,
            cost_usd=0.02,
        )

        stats = await get_daily_stats(user_id=1)
        assert stats["total_requests"] == 2
        assert stats["total_tokens_in"] == 400
        assert stats["total_tokens_out"] == 600
        assert stats["total_reasoning_tokens"] == 150
        assert stats["total_cost_usd"] == pytest.approx(0.03)


# ---------------------------------------------------------------------------
# set_user_setting / get_user_setting
# ---------------------------------------------------------------------------

class TestUserSettings:
    @pytest.mark.asyncio
    async def test_set_and_get_user_setting(self) -> None:
        await set_user_setting(user_id=42, key="reasoning_effort", value="low")
        result = await get_user_setting(user_id=42, key="reasoning_effort")
        assert result == "low"

    @pytest.mark.asyncio
    async def test_get_nonexistent_setting(self) -> None:
        result = await get_user_setting(user_id=99, key="reasoning_effort")
        assert result is None


# ---------------------------------------------------------------------------
# dynamic_users
# ---------------------------------------------------------------------------

class TestDynamicUsers:
    @pytest.mark.asyncio
    async def test_dynamic_user_add_remove(self) -> None:
        # Initially not allowed
        assert await is_dynamic_user_allowed(100) is False

        # Add user
        assert await add_dynamic_user(user_id=100, added_by=1) is True
        assert await is_dynamic_user_allowed(100) is True

        # Remove user
        removed = await remove_dynamic_user(100)
        assert removed == 1
        assert await is_dynamic_user_allowed(100) is False
