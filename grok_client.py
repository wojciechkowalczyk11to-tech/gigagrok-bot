"""xAI / Grok API client with streaming support."""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncGenerator

import httpx
import structlog

logger = structlog.get_logger(__name__)

# Retry settings
_MAX_RETRIES: int = 3
_RETRY_DELAYS: tuple[float, ...] = (1.0, 2.0, 4.0)
_RATE_LIMIT_DELAY: float = 5.0


class GrokClient:
    """Async HTTP client for the xAI chat completions API."""

    def __init__(self, api_key: str, base_url: str = "https://api.x.ai/v1") -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0),
            headers={
                "Authorization": f"Bearer {api_key}",
            },
        )
        self._semaphore = asyncio.Semaphore(5)

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------
    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        model: str,
        max_tokens: int = 16000,
        reasoning_effort: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        search: dict[str, Any] | None = None,
    ) -> AsyncGenerator[tuple[str, Any], None]:
        """Yield ``(event_type, data)`` tuples from a streaming chat request.

        Event types:
        * ``("reasoning", chunk_text)``
        * ``("content", chunk_text)``
        * ``("status", status_msg)``
        * ``("done", usage_dict)``
        """
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "max_tokens": max_tokens,
        }

        # reasoning param ONLY for models known to support effort
        if reasoning_effort and model.startswith("grok-3-mini"):
            body["reasoning"] = {"effort": reasoning_effort}

        if tools:
            body["tools"] = tools

        if search:
            body.update(search)

        last_error: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            rate_limited = False
            try:
                async with self._semaphore:
                    async with self._client.stream(
                        "POST",
                        f"{self._base_url}/chat/completions",
                        json=body,
                    ) as response:
                        if response.status_code == 429:
                            await response.aread()
                            rate_limited = True
                        elif response.status_code != 200:
                            error_body = await response.aread()
                            raise httpx.HTTPStatusError(
                                f"HTTP {response.status_code}: {error_body.decode(errors='replace')}",
                                request=response.request,
                                response=response,
                            )
                        else:
                            async for line in response.aiter_lines():
                                line = line.strip()
                                if not line or not line.startswith("data: "):
                                    continue

                                payload = line[6:]  # strip "data: "
                                if payload == "[DONE]":
                                    break

                                try:
                                    chunk = json.loads(payload)
                                except json.JSONDecodeError:
                                    continue

                                choices = chunk.get("choices", [])
                                if not choices:
                                    continue

                                choice = choices[0]
                                delta = choice.get("delta", {})

                                # Reasoning tokens come first
                                if "reasoning_content" in delta and delta["reasoning_content"]:
                                    yield ("reasoning", delta["reasoning_content"])

                                tool_calls = delta.get("tool_calls")
                                if isinstance(tool_calls, list):
                                    for tool_call in tool_calls:
                                        if not isinstance(tool_call, dict):
                                            continue
                                        function_data = tool_call.get("function", {})
                                        if not isinstance(function_data, dict):
                                            continue
                                        tool_name = function_data.get("name")
                                        if isinstance(tool_name, str) and tool_name:
                                            yield ("tool_use", tool_name)

                                if "content" in delta and delta["content"]:
                                    yield ("content", delta["content"])

                                # Usage in final chunk
                                usage_raw = chunk.get("usage")
                                if usage_raw:
                                    details = usage_raw.get("completion_tokens_details", {})
                                    yield (
                                        "done",
                                        {
                                            "prompt_tokens": usage_raw.get("prompt_tokens", 0),
                                            "completion_tokens": usage_raw.get("completion_tokens", 0),
                                            "reasoning_tokens": details.get("reasoning_tokens", 0),
                                        },
                                    )
                            # Successful — exit retry loop
                            return

                # Semaphore released — handle 429 retry outside
                if rate_limited:
                    logger.warning(
                        "grok_rate_limited",
                        attempt=attempt + 1,
                        delay=_RATE_LIMIT_DELAY,
                    )
                    await asyncio.sleep(_RATE_LIMIT_DELAY)
                    continue

            except Exception as exc:
                last_error = exc
                if attempt < _MAX_RETRIES - 1:
                    delay = _RETRY_DELAYS[attempt]
                    logger.warning(
                        "grok_stream_retry",
                        attempt=attempt + 1,
                        delay=delay,
                        error=str(exc),
                    )
                    await asyncio.sleep(delay)

        # All retries exhausted
        if last_error:
            logger.error("grok_stream_failed", error=str(last_error))
            raise last_error
        raise RuntimeError("xAI API rate limit — wszystkie próby wyczerpane")

    # ------------------------------------------------------------------
    # Non-streaming
    # ------------------------------------------------------------------
    async def chat(
        self,
        messages: list[dict[str, Any]],
        model: str,
        max_tokens: int = 16000,
        reasoning_effort: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        search: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a non-streaming chat request and return the full response."""
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "max_tokens": max_tokens,
        }

        if reasoning_effort and model.startswith("grok-3-mini"):
            body["reasoning"] = {"effort": reasoning_effort}

        if tools:
            body["tools"] = tools

        if search:
            body.update(search)

        last_error: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                async with self._semaphore:
                    resp = await self._client.post(
                        f"{self._base_url}/chat/completions",
                        json=body,
                    )
                # Semaphore released — handle 429 outside
                if resp.status_code == 429:
                    logger.warning(
                        "grok_rate_limited",
                        attempt=attempt + 1,
                        delay=_RATE_LIMIT_DELAY,
                    )
                    await asyncio.sleep(_RATE_LIMIT_DELAY)
                    continue
                resp.raise_for_status()
                return resp.json()  # type: ignore[no-any-return]
            except Exception as exc:
                last_error = exc
                if attempt < _MAX_RETRIES - 1:
                    delay = _RETRY_DELAYS[attempt]
                    logger.warning(
                        "grok_chat_retry",
                        attempt=attempt + 1,
                        delay=delay,
                        error=str(exc),
                    )
                    await asyncio.sleep(delay)

        if last_error:
            logger.error("grok_chat_failed", error=str(last_error))
            raise last_error
        raise RuntimeError("Unexpected: no response and no error")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def close(self) -> None:
        """Gracefully close the underlying HTTP client."""
        await self._client.aclose()
