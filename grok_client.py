"""xAI / Grok API client with streaming support."""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncGenerator

import httpx
import structlog

logger = structlog.get_logger(__name__)

_MAX_RETRIES: int = 3
_RETRY_DELAYS: tuple[float, ...] = (1.0, 2.0, 4.0)
_RATE_LIMIT_DELAY: float = 5.0


class GrokClient:
    """Async HTTP client for xAI ``/chat/completions`` requests."""

    def __init__(self, api_key: str, base_url: str = "https://api.x.ai/v1") -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=15.0, read=120.0, write=30.0, pool=15.0),
            headers={"Authorization": f"Bearer {api_key}"},
        )
        self._semaphore = asyncio.Semaphore(5)

    def _build_chat_body(
        self,
        messages: list[dict[str, Any]],
        model: str,
        *,
        stream: bool,
        max_tokens: int,
        reasoning_effort: str | None,
        tools: list[dict[str, Any]] | None,
        search: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Build a chat body with a single, validated shape."""
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": stream,
            "max_tokens": max_tokens,
        }
        if reasoning_effort:
            body["reasoning"] = {"effort": reasoning_effort}
        if tools:
            body["tools"] = tools
        if search:
            logger.warning("legacy_search_param_used")
            body.update(search)
        return body

    async def _sleep_before_retry(self, attempt: int, *, reason: str) -> None:
        if reason == "rate_limit":
            await asyncio.sleep(_RATE_LIMIT_DELAY)
            return
        delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
        await asyncio.sleep(delay)

    def _extract_tool_name(self, tool_call: dict[str, Any]) -> str | None:
        """Extract tool name from streaming tool_call deltas."""
        if not isinstance(tool_call, dict):
            return None
        tool_type = tool_call.get("type")
        if isinstance(tool_type, str) and tool_type:
            return tool_type
        function_data = tool_call.get("function")
        if isinstance(function_data, dict):
            function_name = function_data.get("name")
            if isinstance(function_name, str) and function_name:
                return function_name
        return None

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        model: str,
        max_tokens: int = 16000,
        reasoning_effort: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        search: dict[str, Any] | None = None,
    ) -> AsyncGenerator[tuple[str, Any], None]:
        """Yield ``(event_type, data)`` tuples from a streaming chat request."""
        body = self._build_chat_body(
            messages,
            model,
            stream=True,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
            tools=tools,
            search=search,
        )

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
                                payload_line = line.strip()
                                if not payload_line.startswith("data: "):
                                    continue
                                payload = payload_line[6:]
                                if payload == "[DONE]":
                                    break
                                try:
                                    chunk = json.loads(payload)
                                except json.JSONDecodeError:
                                    logger.warning("stream_json_decode_failed")
                                    continue

                                choices = chunk.get("choices")
                                if not isinstance(choices, list) or not choices:
                                    continue
                                choice = choices[0]
                                if not isinstance(choice, dict):
                                    continue
                                delta = choice.get("delta")
                                if not isinstance(delta, dict):
                                    continue

                                reasoning_chunk = delta.get("reasoning_content")
                                if isinstance(reasoning_chunk, str) and reasoning_chunk:
                                    yield ("reasoning", reasoning_chunk)

                                tool_calls = delta.get("tool_calls")
                                if isinstance(tool_calls, list):
                                    for tool_call in tool_calls:
                                        if isinstance(tool_call, dict):
                                            tool_name = self._extract_tool_name(
                                                tool_call
                                            )
                                            if tool_name:
                                                yield ("tool_use", tool_name)

                                content_chunk = delta.get("content")
                                if isinstance(content_chunk, str) and content_chunk:
                                    yield ("content", content_chunk)

                                usage_raw = chunk.get("usage")
                                if isinstance(usage_raw, dict):
                                    details = usage_raw.get("completion_tokens_details")
                                    completion_details = (
                                        details if isinstance(details, dict) else {}
                                    )
                                    yield (
                                        "done",
                                        {
                                            "prompt_tokens": int(
                                                usage_raw.get("prompt_tokens", 0) or 0
                                            ),
                                            "completion_tokens": int(
                                                usage_raw.get("completion_tokens", 0)
                                                or 0
                                            ),
                                            "reasoning_tokens": int(
                                                completion_details.get(
                                                    "reasoning_tokens", 0
                                                )
                                                or 0
                                            ),
                                        },
                                    )
                            return

                if rate_limited:
                    logger.warning("grok_rate_limited", attempt=attempt + 1)
                    await self._sleep_before_retry(attempt, reason="rate_limit")
            except Exception as exc:
                last_error = exc
                logger.warning("grok_stream_retry", attempt=attempt + 1, error=str(exc))
                if attempt < _MAX_RETRIES - 1:
                    await self._sleep_before_retry(attempt, reason="error")

        if last_error:
            logger.error("grok_stream_failed", error=str(last_error))
            raise last_error
        raise RuntimeError("xAI API rate limit — wszystkie próby wyczerpane")

    async def chat(
        self,
        messages: list[dict[str, Any]],
        model: str,
        max_tokens: int = 16000,
        reasoning_effort: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        search: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a non-streaming chat request and return full JSON response."""
        body = self._build_chat_body(
            messages,
            model,
            stream=False,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
            tools=tools,
            search=search,
        )

        last_error: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                async with self._semaphore:
                    response = await self._client.post(
                        f"{self._base_url}/chat/completions", json=body
                    )
                if response.status_code == 429:
                    logger.warning("grok_rate_limited", attempt=attempt + 1)
                    await self._sleep_before_retry(attempt, reason="rate_limit")
                    continue
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise RuntimeError("Nieprawidłowa odpowiedź API xAI.")
                return payload
            except Exception as exc:
                last_error = exc
                logger.warning("grok_chat_retry", attempt=attempt + 1, error=str(exc))
                if attempt < _MAX_RETRIES - 1:
                    await self._sleep_before_retry(attempt, reason="error")

        if last_error:
            logger.error("grok_chat_failed", error=str(last_error))
            raise last_error
        raise RuntimeError("Unexpected: no response and no error")

    async def search_collection(
        self,
        collection_id: str,
        query: str,
        max_results: int = 10,
    ) -> list[dict[str, Any]]:
        """Search a collection via ``POST /documents/search``."""
        body: dict[str, Any] = {
            "query": query,
            "source": {"collection_ids": [collection_id]},
        }

        last_error: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                async with self._semaphore:
                    response = await self._client.post(
                        f"{self._base_url}/documents/search", json=body
                    )

                if response.status_code == 429:
                    logger.warning(
                        "collection_search_rate_limited", attempt=attempt + 1
                    )
                    await self._sleep_before_retry(attempt, reason="rate_limit")
                    continue

                response.raise_for_status()
                data = response.json()

                if isinstance(data, list):
                    results = [item for item in data if isinstance(item, dict)]
                elif isinstance(data, dict):
                    rows = (
                        data.get("results")
                        if isinstance(data.get("results"), list)
                        else data.get("data")
                    )
                    results = (
                        [item for item in rows if isinstance(item, dict)]
                        if isinstance(rows, list)
                        else []
                    )
                else:
                    results = []

                return results[:max_results]
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "collection_search_retry", attempt=attempt + 1, error=str(exc)
                )
                if attempt < _MAX_RETRIES - 1:
                    await self._sleep_before_retry(attempt, reason="error")

        if last_error:
            logger.error("collection_search_failed", error=str(last_error))
            raise last_error
        raise RuntimeError("collection search — wszystkie próby wyczerpane")

    async def close(self) -> None:
        """Gracefully close the underlying HTTP client."""
        await self._client.aclose()
