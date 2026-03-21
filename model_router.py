"""Intelligent multi-model router with circuit breaker and fallback.

Architecture aligned with nexus-omega-core Provider Factory pattern:
- ECO / SMART / DEEP profiles per provider
- Fallback chain with circuit breakers
- Cost tracking per provider/model
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ModelProvider(str, Enum):
    XAI_GROK = "xai_grok"
    DEEPSEEK = "deepseek"
    GEMINI = "gemini"
    CLAUDE = "claude"
    GROQ = "groq"
    MISTRAL = "mistral"


class Profile(str, Enum):
    """Quality/cost profiles (aligned with N.O.C eco/smart/deep)."""
    ECO = "eco"
    SMART = "smart"
    DEEP = "deep"


class QueryComplexity(str, Enum):
    SIMPLE = "simple"
    MODERATE = "moderate"
    COMPLEX = "complex"
    REASONING = "reasoning"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ModelPricing:
    """Per-1M-token pricing in USD."""
    input: float = 0.0
    output: float = 0.0

    def calculate(self, input_tokens: int, output_tokens: int, reasoning_tokens: int = 0) -> float:
        return round(
            (input_tokens / 1_000_000) * self.input
            + ((output_tokens + reasoning_tokens) / 1_000_000) * self.output,
            6,
        )


@dataclass
class ProviderConfig:
    """Configuration for a single AI provider (aligned with N.O.C BaseProvider)."""
    provider: ModelProvider
    api_key: str
    base_url: str
    profile_models: dict[Profile, str]
    pricing: dict[str, ModelPricing]
    capabilities: frozenset[str] = field(default_factory=frozenset)
    max_context: int = 128_000
    max_output: int = 16_000
    rate_limit_rpm: int = 60
    priority: int = 10
    is_available: bool = True

    def model_for_profile(self, profile: Profile) -> str:
        return self.profile_models.get(profile, next(iter(self.profile_models.values())))

    def cost(self, model: str, input_tokens: int, output_tokens: int, reasoning_tokens: int = 0) -> float:
        p = self.pricing.get(model, ModelPricing())
        return p.calculate(input_tokens, output_tokens, reasoning_tokens)


@dataclass
class CircuitBreaker:
    failure_threshold: int = 3
    recovery_timeout: float = 60.0
    _failure_count: int = field(default=0, repr=False)
    _last_failure: float = field(default=0.0, repr=False)
    _is_open: bool = field(default=False, repr=False)

    @property
    def is_open(self) -> bool:
        if self._is_open and (time.time() - self._last_failure) > self.recovery_timeout:
            self._is_open = False
            self._failure_count = 0
            logger.info("circuit_breaker_half_open")
        return self._is_open

    def record_success(self) -> None:
        self._failure_count = 0
        self._is_open = False

    def record_failure(self) -> None:
        self._failure_count += 1
        self._last_failure = time.time()
        if self._failure_count >= self.failure_threshold:
            self._is_open = True
            logger.warning(
                "circuit_breaker_opened",
                failures=self._failure_count,
                recovery_in=self.recovery_timeout,
            )


# ---------------------------------------------------------------------------
# Default provider configs (xAI pricing as of 2026-03)
# ---------------------------------------------------------------------------

def default_xai_config(api_key: str) -> ProviderConfig:
    return ProviderConfig(
        provider=ModelProvider.XAI_GROK,
        api_key=api_key,
        base_url="https://api.x.ai/v1",
        profile_models={
            Profile.ECO: "grok-4.20-0309-non-reasoning",
            Profile.SMART: "grok-4.20-0309-reasoning",
            Profile.DEEP: "grok-4.20-0309-reasoning",
        },
        pricing={
            "grok-4.20-0309-reasoning": ModelPricing(input=2.0, output=10.0),
            "grok-4.20-0309-non-reasoning": ModelPricing(input=2.0, output=10.0),
            "grok-4-1-fast-reasoning": ModelPricing(input=0.20, output=0.50),
        },
        capabilities=frozenset({"reasoning", "tools", "search", "vision", "mcp"}),
        max_context=2_000_000,
        priority=1,
    )


# ---------------------------------------------------------------------------
# Query classifier
# ---------------------------------------------------------------------------

def classify_query(text: str) -> QueryComplexity:
    """Classify query complexity using heuristics."""
    text_lower = text.lower()
    word_count = len(text.split())

    reasoning_kw = {
        "dlaczego", "why", "explain", "wyjaśnij", "przeanalizuj", "analyze",
        "porównaj", "compare", "oceń", "evaluate", "rozwiąż", "solve",
        "udowodnij", "prove", "zoptymalizuj", "optimize",
    }
    complex_kw = {
        "napisz kod", "write code", "zaimplementuj", "implement",
        "zaprojektuj", "design", "architektura", "architecture",
        "refaktoruj", "refactor", "debug", "review",
    }
    simple_kw = {
        "cześć", "hello", "hi", "hej", "co to", "what is",
        "przetłumacz", "translate", "podsumuj", "summarize",
    }

    if any(kw in text_lower for kw in reasoning_kw) or word_count > 200:
        return QueryComplexity.REASONING
    if any(kw in text_lower for kw in complex_kw) or word_count > 80:
        return QueryComplexity.COMPLEX
    if any(kw in text_lower for kw in simple_kw) or word_count < 15:
        return QueryComplexity.SIMPLE
    return QueryComplexity.MODERATE


def complexity_to_profile(complexity: QueryComplexity) -> Profile:
    """Map query complexity to cost/quality profile."""
    return {
        QueryComplexity.SIMPLE: Profile.ECO,
        QueryComplexity.MODERATE: Profile.SMART,
        QueryComplexity.COMPLEX: Profile.DEEP,
        QueryComplexity.REASONING: Profile.DEEP,
    }[complexity]


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

class ModelRouter:
    """Route requests to best available model (aligned with N.O.C factory)."""

    def __init__(self) -> None:
        self._configs: dict[ModelProvider, ProviderConfig] = {}
        self._breakers: dict[ModelProvider, CircuitBreaker] = {}
        self._lock = asyncio.Lock()

    def register(self, config: ProviderConfig) -> None:
        self._configs[config.provider] = config
        if config.provider not in self._breakers:
            self._breakers[config.provider] = CircuitBreaker()

    def unregister(self, provider: ModelProvider) -> None:
        self._configs.pop(provider, None)

    @property
    def available_providers(self) -> list[ModelProvider]:
        return [
            p for p, c in self._configs.items()
            if c.is_available and not self._breakers[p].is_open
        ]

    def select(
        self,
        profile: Profile = Profile.SMART,
        needs_tools: bool = False,
        needs_vision: bool = False,
        needs_search: bool = False,
        preferred: ModelProvider | None = None,
    ) -> tuple[ProviderConfig, str] | None:
        """Select provider + model name for given profile and capabilities.

        Returns ``(config, model_name)`` or ``None`` if nothing available.
        """
        candidates: list[ProviderConfig] = []

        for provider, config in self._configs.items():
            if not config.is_available:
                continue
            breaker = self._breakers.get(provider)
            if breaker and breaker.is_open:
                continue

            caps = config.capabilities
            if needs_tools and "tools" not in caps:
                continue
            if needs_vision and "vision" not in caps:
                continue
            if needs_search and "search" not in caps:
                continue

            candidates.append(config)

        if not candidates:
            logger.warning("no_available_models")
            return None

        if preferred:
            for c in candidates:
                if c.provider == preferred:
                    return c, c.model_for_profile(profile)

        # ECO: cheapest first; DEEP: highest-capability first
        if profile == Profile.ECO:
            candidates.sort(key=lambda c: (c.priority, self._avg_cost(c)))
        else:
            candidates.sort(key=lambda c: (c.priority, -c.max_context))

        best = candidates[0]
        return best, best.model_for_profile(profile)

    def select_for_text(
        self,
        text: str,
        needs_tools: bool = False,
        needs_vision: bool = False,
        needs_search: bool = False,
        preferred: ModelProvider | None = None,
    ) -> tuple[ProviderConfig, str] | None:
        """Classify text and select model accordingly."""
        complexity = classify_query(text)
        profile = complexity_to_profile(complexity)
        logger.debug("query_classified", complexity=complexity.value, profile=profile.value)
        return self.select(
            profile=profile,
            needs_tools=needs_tools,
            needs_vision=needs_vision,
            needs_search=needs_search,
            preferred=preferred,
        )

    def record_success(self, provider: ModelProvider) -> None:
        breaker = self._breakers.get(provider)
        if breaker:
            breaker.record_success()

    def record_failure(self, provider: ModelProvider) -> None:
        breaker = self._breakers.get(provider)
        if breaker:
            breaker.record_failure()

    def get_fallback(self, failed_provider: ModelProvider, profile: Profile = Profile.SMART) -> tuple[ProviderConfig, str] | None:
        """Get next best model after a failure (fallback chain)."""
        candidates = [
            c for p, c in self._configs.items()
            if p != failed_provider and c.is_available
            and not self._breakers.get(p, CircuitBreaker()).is_open
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda c: (c.priority, self._avg_cost(c)))
        best = candidates[0]
        return best, best.model_for_profile(profile)

    @staticmethod
    def _avg_cost(config: ProviderConfig) -> float:
        if not config.pricing:
            return 0.0
        total = sum(p.input + p.output for p in config.pricing.values())
        return total / len(config.pricing)

    def status(self) -> dict[str, Any]:
        """Return router status for diagnostics / /status command."""
        return {
            provider.value: {
                "models": {p.value: config.model_for_profile(p) for p in Profile},
                "available": config.is_available,
                "circuit_open": self._breakers.get(provider, CircuitBreaker()).is_open,
                "priority": config.priority,
                "capabilities": sorted(config.capabilities),
            }
            for provider, config in self._configs.items()
        }
