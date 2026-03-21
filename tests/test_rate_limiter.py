"""Tests for rate_limiter module."""

from __future__ import annotations

import asyncio
import time

import pytest

from rate_limiter import RateLimiter, TokenBucket, UserQuota


# ---------------------------------------------------------------------------
# TokenBucket
# ---------------------------------------------------------------------------

class TestTokenBucket:
    @pytest.mark.asyncio
    async def test_token_bucket_acquire_success(self) -> None:
        bucket = TokenBucket(capacity=10, refill_rate=1.0)
        assert await bucket.acquire(5) is True

    @pytest.mark.asyncio
    async def test_token_bucket_acquire_over_capacity(self) -> None:
        bucket = TokenBucket(capacity=5, refill_rate=1.0)
        assert await bucket.acquire(10) is False

    @pytest.mark.asyncio
    async def test_token_bucket_refills(self) -> None:
        bucket = TokenBucket(capacity=10, refill_rate=100.0)  # 100 tokens/sec
        # Drain all tokens
        assert await bucket.acquire(10) is True
        assert await bucket.acquire(1) is False

        # Wait a bit for refill
        await asyncio.sleep(0.15)

        # Should have refilled some tokens
        assert await bucket.acquire(1) is True


# ---------------------------------------------------------------------------
# UserQuota
# ---------------------------------------------------------------------------

class TestUserQuota:
    def test_user_quota_check_passes(self) -> None:
        quota = UserQuota(daily_requests=200, daily_tokens=500_000)
        # Force reset_at to the future so _check_reset doesn't zero counters
        quota.reset_at = time.time() + 86400
        ok, reason = quota.check()
        assert ok is True
        assert reason == ""

    def test_user_quota_check_exceeded_requests(self) -> None:
        quota = UserQuota(daily_requests=10, daily_tokens=500_000)
        quota.reset_at = time.time() + 86400
        quota.used_requests = 10
        ok, reason = quota.check()
        assert ok is False
        assert "zapytań" in reason

    def test_user_quota_check_exceeded_tokens(self) -> None:
        quota = UserQuota(daily_requests=200, daily_tokens=1000)
        quota.reset_at = time.time() + 86400
        quota.used_tokens = 500
        ok, reason = quota.check(estimated_tokens=600)
        assert ok is False
        assert "tokenów" in reason

    def test_user_quota_consume(self) -> None:
        quota = UserQuota()
        quota.reset_at = time.time() + 86400
        quota.consume(tokens=100, cost=0.01)
        assert quota.used_requests == 1
        assert quota.used_tokens == 100
        assert quota.used_cost_usd == pytest.approx(0.01)

    def test_user_quota_remaining(self) -> None:
        quota = UserQuota(daily_requests=200, daily_tokens=500_000, daily_cost_usd=5.0)
        quota.reset_at = time.time() + 86400
        quota.consume(tokens=1000, cost=0.50)
        remaining = quota.remaining()
        assert remaining["requests"] == 199
        assert remaining["tokens"] == 499_000
        assert remaining["cost_usd"] == pytest.approx(4.50)


# ---------------------------------------------------------------------------
# RateLimiter (integration)
# ---------------------------------------------------------------------------

class TestRateLimiter:
    @pytest.mark.asyncio
    async def test_rate_limiter_check_and_acquire(self) -> None:
        limiter = RateLimiter()
        limiter.add_model_limit("test-model", rpm=60)

        ok, reason = await limiter.check_and_acquire(
            user_id=123,
            model_name="test-model",
            estimated_tokens=100,
            estimated_cost=0.01,
        )
        assert ok is True
        assert reason == ""

    @pytest.mark.asyncio
    async def test_rate_limiter_record_usage(self) -> None:
        limiter = RateLimiter()
        limiter.record_usage(user_id=123, tokens=500, cost=0.05)

        remaining = limiter.get_user_remaining(123)
        assert remaining["requests"] == 199
        assert remaining["tokens"] == 499_500
