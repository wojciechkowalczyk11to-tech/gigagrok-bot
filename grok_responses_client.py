"""xAI Responses API client — native MCP + ask_claude tool bridge."""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncGenerator

import httpx
import structlog

logger = structlog.get_logger(__name__)

_MAX_RETRIES = 3
_RETRY_DELAYS = (1.0, 2.0, 4.0)
_ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
_XAI_RESPONSES_URL = "https://api.x.ai/v1/responses"


class GrokResponsesClient:
    """Async client for xAI /v1/responses with Remote MCP + Claude tool."""

    def __init__(
        self,
        api_key: str,
        nexus_mcp_url: str = "",
        nexus_auth_token: str = "",
        anthropic_api_key: str = "",
    ) -> None:
        self.api_key = api_key
        self.nexus_mcp_url = nexus_mcp_url
        self.nexus_auth_token = nexus_auth_token
        self.anthropic_api_key = anthropic_api_key

        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=15.0, read=180.0, write=30.0, pool=15.0),
            headers={"Authorization": f"Bearer {api_key}"},
        )
        self._semaphore = asyncio.Semaphore(5)

    # ------------------------------------------------------------------
    # Tools definition
    # ------------------------------------------------------------------

    def _build_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []

        if self.nexus_mcp_url:
            mcp_tool: dict[str, Any] = {
                "type": "mcp",
                "server_url": self.nexus_mcp_url,
                "server_label": "nexus",
                "server_description": "NEXUS MCP — 44 tools: GCP VM, Cloudflare DNS, GitHub, Docker, AI proxy, xAI collections, web fetch",
            }
            if self.nexus_auth_token:
                mcp_tool["authorization"] = f"Bearer {self.nexus_auth_token}"
            tools.append(mcp_tool)

        tools.append({
            "type": "function",
            "function": {
                "name": "ask_claude",
                "description": (
                    "Delegate to Claude Sonnet (Anthropic) for tasks requiring deep analysis, "
                    "long-form reasoning, document synthesis, code review, or nuanced writing. "
                    "Use when Grok needs a second opinion or specialised assistance."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": "Full question or task context to send to Claude",
                        }
                    },
                    "required": ["prompt"],
                },
            },
        })

        return tools

    # ------------------------------------------------------------------
    # Claude bridge
    # ------------------------------------------------------------------

    async def _execute_ask_claude(self, prompt: str) -> str:
        """Call Anthropic claude-sonnet-4-20250514 and return text response."""
        if not self.anthropic_api_key:
            return "[ask_claude] ANTHROPIC_API_KEY not configured."

        payload = {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": prompt}],
        }
        headers = {
            "x-api-key": self.anthropic_api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        last_error: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as ac:
                    resp = await ac.post(_ANTHROPIC_API_URL, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                return data["content"][0]["text"]
            except Exception as exc:
                last_error = exc
                logger.warning("ask_claude_retry", attempt=attempt + 1, error=str(exc))
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(_RETRY_DELAYS[attempt])

        return f"[ask_claude] Error after {_MAX_RETRIES} retries: {last_error}"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_payload(
        self,
        messages: list[dict[str, Any]],
        model: str,
        max_tokens: int,
        stream: bool,
        include_tools: bool,
    ) -> dict[str, Any]:
        """Convert messages list to Responses API `input` format."""
        # Separate system prompt from conversation
        input_items: list[dict[str, Any]] = []
        system_content = ""

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "system":
                system_content = content
                continue
            if role in ("user", "assistant"):
                input_items.append({"role": role, "content": content})
            elif role == "tool":
                # Tool result: Responses API format
                input_items.append({
                    "type": "function_call_output",
                    "call_id": msg.get("call_id", msg.get("tool_call_id", "")),
                    "output": content,
                })

        body: dict[str, Any] = {
            "model": model,
            "input": input_items,
            "max_output_tokens": max_tokens,
            "stream": stream,
        }
        if system_content:
            body["instructions"] = system_content
        if include_tools:
            body["tools"] = self._build_tools()

        return body

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        model: str,
        max_tokens: int = 16000,
        reasoning_effort: str | None = None,  # kept for API compat, ignored for Grok 4
        tools: list | None = None,  # kept for compat, use nexus_mcp_url instead
        search: dict | None = None,
    ) -> AsyncGenerator[tuple[str, Any], None]:
        """Yield (event_type, data) from Responses API SSE stream.

        event_type values: 'reasoning', 'content', 'tool_call', 'done'
        """
        body = self._build_payload(
            messages, model, max_tokens, stream=True, include_tools=True
        )

        pending_tool_calls: dict[str, dict[str, Any]] = {}
        completed_tool_calls: list[dict[str, Any]] = []
        usage: dict[str, int] = {}
        function_call_output_items: list[dict[str, Any]] = []

        last_error: Exception | None = None

        for attempt in range(_MAX_RETRIES):
            rate_limited = False
            try:
                async with self._semaphore:
                    async with self._client.stream(
                        "POST", _XAI_RESPONSES_URL, json=body
                    ) as response:
                        if response.status_code == 429:
                            await response.aread()
                            rate_limited = True
                        elif response.status_code != 200:
                            err = await response.aread()
                            raise httpx.HTTPStatusError(
                                f"HTTP {response.status_code}: {err.decode(errors='replace')}",
                                request=response.request,
                                response=response,
                            )
                        else:
                            async for line in response.aiter_lines():
                                line = line.strip()
                                if not line or not line.startswith("data:"):
                                    continue
                                data_str = line[5:].strip()
                                if data_str == "[DONE]":
                                    break
                                try:
                                    chunk = json.loads(data_str)
                                except json.JSONDecodeError:
                                    continue

                                ctype = chunk.get("type", "")

                                if ctype == "response.output_text.delta":
                                    yield "content", chunk.get("delta", "")

                                elif ctype == "response.reasoning_summary_text.delta":
                                    yield "reasoning", chunk.get("delta", "")

                                elif ctype == "response.output_item.added":
                                    item = chunk.get("item", {})
                                    if item.get("type") == "function_call":
                                        item_id = item.get("id", "")
                                        pending_tool_calls[item_id] = {
                                            "id": item_id,
                                            "call_id": item.get("call_id", item_id),
                                            "name": item.get("name", ""),
                                            "arguments": "",
                                        }

                                elif ctype == "response.function_call_arguments.delta":
                                    item_id = chunk.get("item_id", "")
                                    if item_id in pending_tool_calls:
                                        pending_tool_calls[item_id]["arguments"] += chunk.get("delta", "")

                                elif ctype == "response.function_call_arguments.done":
                                    item_id = chunk.get("item_id", "")
                                    if item_id in pending_tool_calls:
                                        tc = pending_tool_calls[item_id]
                                        tc["arguments"] = chunk.get("arguments", tc["arguments"])
                                        completed_tool_calls.append(tc)
                                        yield "tool_call", {"name": tc["name"]}

                                elif ctype == "response.done":
                                    resp_data = chunk.get("response", {})
                                    u = resp_data.get("usage", {})
                                    usage = {
                                        "prompt_tokens": u.get("input_tokens", 0),
                                        "completion_tokens": u.get("output_tokens", 0),
                                        "reasoning_tokens": u.get("reasoning_tokens", 0),
                                    }

                            # ---- handle ask_claude tool calls ----
                            for tc in completed_tool_calls:
                                if tc["name"] == "ask_claude":
                                    try:
                                        args = json.loads(tc["arguments"] or "{}")
                                    except json.JSONDecodeError:
                                        args = {}
                                    prompt_text = args.get("prompt", "")
                                    logger.info("ask_claude_called", prompt_len=len(prompt_text))
                                    claude_result = await self._execute_ask_claude(prompt_text)

                                    # Build follow-up with tool result
                                    followup_messages = list(messages) + [
                                        {
                                            "role": "assistant",
                                            "content": "",
                                            "tool_calls": [{
                                                "id": tc["call_id"],
                                                "type": "function",
                                                "function": {
                                                    "name": tc["name"],
                                                    "arguments": tc["arguments"],
                                                },
                                            }],
                                        },
                                        {
                                            "role": "tool",
                                            "call_id": tc["call_id"],
                                            "content": claude_result,
                                        },
                                    ]
                                    async for evt, dat in self.chat_stream(
                                        followup_messages, model, max_tokens
                                    ):
                                        yield evt, dat
                                    return

                            yield "done", usage
                            return

                if rate_limited:
                    logger.warning("responses_rate_limited", attempt=attempt + 1)
                    await asyncio.sleep(5.0 if attempt == 0 else _RETRY_DELAYS[attempt])

            except Exception as exc:
                last_error = exc
                logger.warning("responses_stream_retry", attempt=attempt + 1, error=str(exc))
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(_RETRY_DELAYS[attempt])

        err = last_error or RuntimeError("xAI Responses API — all retries exhausted")
        logger.error("responses_stream_failed", error=str(err))
        raise err

    # ------------------------------------------------------------------
    # Non-streaming (fallback / simple queries)
    # ------------------------------------------------------------------

    async def chat(
        self,
        messages: list[dict[str, Any]],
        model: str,
        max_tokens: int = 16000,
        reasoning_effort: str | None = None,
        tools: list | None = None,
        search: dict | None = None,
    ) -> dict[str, Any]:
        """Non-streaming request. Returns dict with content + usage."""
        body = self._build_payload(
            messages, model, max_tokens, stream=False, include_tools=False
        )

        last_error: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                async with self._semaphore:
                    resp = await self._client.post(_XAI_RESPONSES_URL, json=body)
                if resp.status_code == 429:
                    await asyncio.sleep(_RETRY_DELAYS[min(attempt, 2)])
                    continue
                resp.raise_for_status()
                data = resp.json()
                text = ""
                for item in data.get("output", []):
                    if item.get("type") == "message":
                        for part in item.get("content", []):
                            if part.get("type") == "output_text":
                                text += part.get("text", "")
                u = data.get("usage", {})
                return {
                    "content": text,
                    "usage": {
                        "prompt_tokens": u.get("input_tokens", 0),
                        "completion_tokens": u.get("output_tokens", 0),
                        "reasoning_tokens": u.get("reasoning_tokens", 0),
                    },
                }
            except Exception as exc:
                last_error = exc
                logger.warning("responses_chat_retry", attempt=attempt + 1, error=str(exc))
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(_RETRY_DELAYS[attempt])

        raise last_error or RuntimeError("Unexpected: no response")

    async def close(self) -> None:
        await self._client.aclose()
