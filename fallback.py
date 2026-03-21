"""Graceful degradation with fallback chain for AI model failures.

Aligned with nexus-omega-core's ``generate_with_fallback`` pattern:
- Tries providers in priority order until one succeeds
- Tracks attempts for debugging (AllProvidersFailedError style)
- Auto-truncates context in degraded mode
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog

from model_router import ModelRouter, ModelProvider, Profile, ProviderConfig

logger = structlog.get_logger(__name__)


class DegradationLevel(str, Enum):
    FULL = "full"
    LIMITED = "limited"
    DEGRADED = "degraded"
    MINIMAL = "minimal"


@dataclass
class FallbackAttempt:
    """Record of a single fallback attempt (for debugging)."""
    provider: str
    model: str
    error: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class FallbackResult:
    """Result from a fallback attempt."""
    content: str
    provider: ModelProvider | None = None
    model_name: str = ""
    degradation_level: DegradationLevel = DegradationLevel.FULL
    was_fallback: bool = False
    attempts: list[FallbackAttempt] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)


class FallbackManager:
    """Manage graceful degradation when AI models fail.

    Mirrors the ``generate_with_fallback`` approach from nexus-omega-core:
    iterate through a priority-ordered provider chain, recording each failure,
    until one succeeds or all are exhausted.
    """

    MINIMAL_RESPONSES: dict[str, str] = {
        "greeting": "Cześć! Mam chwilowe problemy techniczne. Spróbuj ponownie za moment. 🔧",
        "question": "Przepraszam, ale chwilowo nie mogę przetworzyć zapytań. Spróbuj ponownie za chwilę. ⏳",
        "default": "Usługi AI chwilowo niedostępne. Pracujemy nad przywróceniem. 🔧",
    }

    def __init__(self, router: ModelRouter) -> None:
        self._router = router
        self._level = DegradationLevel.FULL
        self._failure_log: list[FallbackAttempt] = []

    @property
    def level(self) -> DegradationLevel:
        return self._level

    def _update_level(self) -> None:
        available = self._router.available_providers
        total = len(self._router._configs)
        if not total:
            self._level = DegradationLevel.MINIMAL
        elif len(available) == total:
            self._level = DegradationLevel.FULL
        elif len(available) >= total // 2:
            self._level = DegradationLevel.LIMITED
        elif available:
            self._level = DegradationLevel.DEGRADED
        else:
            self._level = DegradationLevel.MINIMAL

    def record_failure(
        self, provider: ModelProvider, error: Exception, model: str = ""
    ) -> None:
        self._router.record_failure(provider)
        attempt = FallbackAttempt(
            provider=provider.value, model=model, error=str(error)
        )
        self._failure_log.append(attempt)
        self._failure_log = self._failure_log[-100:]
        self._update_level()
        logger.warning(
            "model_failure_recorded",
            provider=provider.value,
            model=model,
            level=self._level.value,
            error=str(error),
        )

    def record_success(self, provider: ModelProvider) -> None:
        self._router.record_success(provider)
        self._update_level()

    def get_fallback_model(
        self, failed_provider: ModelProvider, profile: Profile = Profile.SMART,
    ) -> tuple[ProviderConfig, str] | None:
        """Get next available model from the router's fallback chain."""
        return self._router.get_fallback(failed_provider, profile=profile)

    def get_minimal_response(self, user_text: str) -> FallbackResult:
        """Generate a minimal response when all models are unavailable."""
        text_lower = user_text.lower().strip()

        greetings = {"cześć", "hej", "hello", "hi", "hey", "siema", "witaj"}
        if any(g in text_lower for g in greetings):
            msg = self.MINIMAL_RESPONSES["greeting"]
        elif "?" in user_text:
            msg = self.MINIMAL_RESPONSES["question"]
        else:
            msg = self.MINIMAL_RESPONSES["default"]

        return FallbackResult(
            content=msg,
            degradation_level=DegradationLevel.MINIMAL,
            was_fallback=True,
        )

    def truncate_for_degradation(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Truncate message history when in degraded mode."""
        if self._level == DegradationLevel.FULL:
            return messages
        if self._level == DegradationLevel.LIMITED:
            max_history = 10
        elif self._level == DegradationLevel.DEGRADED:
            max_history = 3
        else:
            max_history = 1

        # Keep system prompt + last N messages
        system = [m for m in messages if m.get("role") == "system"]
        others = [m for m in messages if m.get("role") != "system"]
        return system + others[-max_history:]

    def status(self) -> dict[str, Any]:
        return {
            "level": self._level.value,
            "recent_failures": len(self._failure_log),
            "available_models": [p.value for p in self._router.available_providers],
        }
