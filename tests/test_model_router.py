"""Tests for model_router module."""

from __future__ import annotations

import time

import pytest

from model_router import (
    CircuitBreaker,
    ModelPricing,
    ModelProvider,
    ModelRouter,
    Profile,
    ProviderConfig,
    QueryComplexity,
    classify_query,
    complexity_to_profile,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_config(
    provider: ModelProvider = ModelProvider.XAI_GROK,
    priority: int = 1,
    capabilities: list[str] | None = None,
) -> ProviderConfig:
    return ProviderConfig(
        provider=provider,
        api_key="test-key",
        base_url="https://test.example.com",
        profile_models={
            Profile.ECO: "test-eco",
            Profile.SMART: "test-smart",
            Profile.DEEP: "test-deep",
        },
        pricing={"test-eco": ModelPricing(0.1, 0.2)},
        capabilities=frozenset(capabilities or ["reasoning", "tools"]),
        priority=priority,
    )


# ---------------------------------------------------------------------------
# classify_query
# ---------------------------------------------------------------------------

class TestClassifyQuery:
    def test_classify_query_simple(self) -> None:
        assert classify_query("hello") == QueryComplexity.SIMPLE
        assert classify_query("cześć") == QueryComplexity.SIMPLE
        assert classify_query("hi") == QueryComplexity.SIMPLE

    def test_classify_query_reasoning(self) -> None:
        assert classify_query("dlaczego niebo jest niebieskie?") == QueryComplexity.REASONING
        assert classify_query("explain how TCP works") == QueryComplexity.REASONING
        assert classify_query("why is the sky blue?") == QueryComplexity.REASONING

    def test_classify_query_complex(self) -> None:
        assert classify_query("napisz kod sortowania") == QueryComplexity.COMPLEX
        assert classify_query("implement a linked list") == QueryComplexity.COMPLEX
        assert classify_query("refactor this module") == QueryComplexity.COMPLEX

    def test_classify_query_moderate(self) -> None:
        # Medium-length text that doesn't match any keyword set → MODERATE
        text = " ".join(["token"] * 20)  # 20 words, > 15
        assert classify_query(text) == QueryComplexity.MODERATE


# ---------------------------------------------------------------------------
# complexity_to_profile
# ---------------------------------------------------------------------------

class TestComplexityToProfile:
    def test_complexity_to_profile(self) -> None:
        assert complexity_to_profile(QueryComplexity.SIMPLE) == Profile.ECO
        assert complexity_to_profile(QueryComplexity.MODERATE) == Profile.SMART
        assert complexity_to_profile(QueryComplexity.COMPLEX) == Profile.DEEP
        assert complexity_to_profile(QueryComplexity.REASONING) == Profile.DEEP


# ---------------------------------------------------------------------------
# ModelRouter
# ---------------------------------------------------------------------------

class TestModelRouter:
    def test_router_register_and_select(self) -> None:
        router = ModelRouter()
        config = _make_config()
        router.register(config)

        result = router.select(Profile.SMART)
        assert result is not None
        cfg, model = result
        assert cfg.provider == ModelProvider.XAI_GROK
        assert model == "test-smart"

    def test_router_select_with_capabilities(self) -> None:
        router = ModelRouter()
        # Provider WITHOUT tools capability
        no_tools = _make_config(
            provider=ModelProvider.DEEPSEEK,
            capabilities=["reasoning"],
        )
        # Provider WITH tools capability
        with_tools = _make_config(
            provider=ModelProvider.XAI_GROK,
            capabilities=["reasoning", "tools"],
        )
        router.register(no_tools)
        router.register(with_tools)

        result = router.select(Profile.SMART, needs_tools=True)
        assert result is not None
        cfg, _ = result
        assert cfg.provider == ModelProvider.XAI_GROK

    def test_router_select_preferred_provider(self) -> None:
        router = ModelRouter()
        router.register(_make_config(provider=ModelProvider.XAI_GROK, priority=1))
        router.register(_make_config(provider=ModelProvider.DEEPSEEK, priority=2))

        result = router.select(Profile.SMART, preferred=ModelProvider.DEEPSEEK)
        assert result is not None
        cfg, _ = result
        assert cfg.provider == ModelProvider.DEEPSEEK

    def test_router_circuit_breaker_opens(self) -> None:
        router = ModelRouter()
        router.register(_make_config())

        for _ in range(3):
            router.record_failure(ModelProvider.XAI_GROK)

        assert router.select(Profile.SMART) is None

    def test_router_circuit_breaker_recovers(self) -> None:
        router = ModelRouter()
        router.register(_make_config())
        breaker = router._breakers[ModelProvider.XAI_GROK]
        breaker.recovery_timeout = 0.0  # instant recovery

        for _ in range(3):
            router.record_failure(ModelProvider.XAI_GROK)

        # Breaker should be open
        assert breaker._is_open is True

        # Simulate time passing beyond recovery timeout
        breaker._last_failure = time.time() - 1.0

        # Now the breaker should auto-close on next check
        assert router.select(Profile.SMART) is not None

    def test_router_get_fallback_excludes_failed(self) -> None:
        router = ModelRouter()
        router.register(_make_config(provider=ModelProvider.XAI_GROK, priority=1))
        router.register(_make_config(provider=ModelProvider.DEEPSEEK, priority=2))

        result = router.get_fallback(ModelProvider.XAI_GROK, Profile.SMART)
        assert result is not None
        cfg, _ = result
        assert cfg.provider == ModelProvider.DEEPSEEK

    def test_router_no_available_returns_none(self) -> None:
        router = ModelRouter()
        assert router.select(Profile.SMART) is None

    def test_router_status(self) -> None:
        router = ModelRouter()
        config = _make_config()
        router.register(config)

        status = router.status()
        assert ModelProvider.XAI_GROK.value in status

        entry = status[ModelProvider.XAI_GROK.value]
        assert entry["available"] is True
        assert entry["circuit_open"] is False
        assert "models" in entry
        assert entry["models"]["eco"] == "test-eco"
        assert entry["priority"] == 1


# ---------------------------------------------------------------------------
# CircuitBreaker standalone
# ---------------------------------------------------------------------------

class TestCircuitBreaker:
    def test_stays_closed_under_threshold(self) -> None:
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb.is_open is False

    def test_opens_at_threshold(self) -> None:
        cb = CircuitBreaker(failure_threshold=3)
        for _ in range(3):
            cb.record_failure()
        assert cb.is_open is True

    def test_success_resets(self) -> None:
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert cb._failure_count == 0
        assert cb.is_open is False
