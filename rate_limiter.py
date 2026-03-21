"""Token-bucket rate limiter and per-user quota management."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class TokenBucket:
    """Classic token-bucket rate limiter."""

    capacity: int
    refill_rate: float  # tokens per second
    _tokens: float = field(init=False)
    _last_refill: float = field(init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def __post_init__(self) -> None:
        self._tokens = float(self.capacity)
        self._last_refill = time.monotonic()

    async def acquire(self, tokens: int = 1) -> bool:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_rate)
            self._last_refill = now
            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False

    async def wait_and_acquire(self, tokens: int = 1, timeout: float = 30.0) -> bool:
        """Block until tokens are available or timeout."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if await self.acquire(tokens):
                return True
            await asyncio.sleep(0.1)
        return False

    @property
    def available(self) -> float:
        return self._tokens


@dataclass
class UserQuota:
    """Per-user daily quota tracking."""

    daily_requests: int = 200
    daily_tokens: int = 500_000
    daily_cost_usd: float = 5.0

    used_requests: int = 0
    used_tokens: int = 0
    used_cost_usd: float = 0.0
    reset_at: float = field(default_factory=lambda: 0.0)

    def _check_reset(self) -> None:
        now = time.time()
        if now >= self.reset_at:
            self.used_requests = 0
            self.used_tokens = 0
            self.used_cost_usd = 0.0
            # Next midnight UTC
            import datetime as dt
            tomorrow = dt.datetime.now(dt.timezone.utc).date() + dt.timedelta(days=1)
            self.reset_at = dt.datetime.combine(
                tomorrow, dt.time.min, tzinfo=dt.timezone.utc
            ).timestamp()

    def check(self, estimated_tokens: int = 0, estimated_cost: float = 0.0) -> tuple[bool, str]:
        """Check if user has remaining quota."""
        self._check_reset()
        if self.used_requests >= self.daily_requests:
            return False, "Dzienny limit zapytań wyczerpany"
        if self.used_tokens + estimated_tokens > self.daily_tokens:
            return False, "Dzienny limit tokenów wyczerpany"
        if self.used_cost_usd + estimated_cost > self.daily_cost_usd:
            return False, "Dzienny limit kosztów wyczerpany"
        return True, ""

    def consume(self, tokens: int, cost: float) -> None:
        self._check_reset()
        self.used_requests += 1
        self.used_tokens += tokens
        self.used_cost_usd += cost

    def remaining(self) -> dict[str, Any]:
        self._check_reset()
        return {
            "requests": max(0, self.daily_requests - self.used_requests),
            "tokens": max(0, self.daily_tokens - self.used_tokens),
            "cost_usd": round(max(0.0, self.daily_cost_usd - self.used_cost_usd), 4),
        }


class RateLimiter:
    """Manages rate limits for multiple models and user quotas."""

    def __init__(self) -> None:
        self._model_buckets: dict[str, TokenBucket] = {}
        self._user_quotas: dict[int, UserQuota] = {}
        self._global_bucket = TokenBucket(capacity=120, refill_rate=2.0)

    def add_model_limit(self, model_name: str, rpm: int) -> None:
        self._model_buckets[model_name] = TokenBucket(
            capacity=rpm, refill_rate=rpm / 60.0
        )

    def set_user_quota(
        self,
        user_id: int,
        daily_requests: int = 200,
        daily_tokens: int = 500_000,
        daily_cost_usd: float = 5.0,
    ) -> None:
        self._user_quotas[user_id] = UserQuota(
            daily_requests=daily_requests,
            daily_tokens=daily_tokens,
            daily_cost_usd=daily_cost_usd,
        )

    def _get_user_quota(self, user_id: int) -> UserQuota:
        if user_id not in self._user_quotas:
            self._user_quotas[user_id] = UserQuota()
        return self._user_quotas[user_id]

    async def check_and_acquire(
        self,
        user_id: int,
        model_name: str,
        estimated_tokens: int = 1000,
        estimated_cost: float = 0.01,
    ) -> tuple[bool, str]:
        """Check all limits and acquire slots if possible."""
        # 1. User quota
        quota = self._get_user_quota(user_id)
        ok, reason = quota.check(estimated_tokens, estimated_cost)
        if not ok:
            logger.warning("quota_exceeded", user_id=user_id, reason=reason)
            return False, reason

        # 2. Global rate limit
        if not await self._global_bucket.acquire():
            return False, "Globalny limit zapytań — spróbuj za chwilę"

        # 3. Model rate limit
        bucket = self._model_buckets.get(model_name)
        if bucket and not await bucket.acquire():
            return False, f"Rate limit modelu {model_name} — spróbuj za chwilę"

        return True, ""

    def record_usage(self, user_id: int, tokens: int, cost: float) -> None:
        quota = self._get_user_quota(user_id)
        quota.consume(tokens, cost)

    def get_user_remaining(self, user_id: int) -> dict[str, Any]:
        return self._get_user_quota(user_id).remaining()

    def status(self) -> dict[str, Any]:
        return {
            "global_available": self._global_bucket.available,
            "models": {
                name: bucket.available for name, bucket in self._model_buckets.items()
            },
            "users_tracked": len(self._user_quotas),
        }
