from __future__ import annotations

from grok_client import GrokClient


def test_build_chat_body_includes_tools() -> None:
    client = GrokClient(api_key="test-key")
    body = client._build_chat_body(
        messages=[{"role": "user", "content": "hi"}],
        model="test-model",
        stream=True,
        max_tokens=42,
        reasoning_effort=None,
        tools=[{"type": "web_search"}],
        search=None,
    )
    assert body["tools"] == [{"type": "web_search"}]


def test_build_chat_body_supports_reasoning_effort() -> None:
    client = GrokClient(api_key="test-key")
    body = client._build_chat_body(
        messages=[{"role": "user", "content": "hi"}],
        model="test-model",
        stream=False,
        max_tokens=77,
        reasoning_effort="medium",
        tools=None,
        search=None,
    )
    assert body["reasoning"] == {"effort": "medium"}


def test_extract_tool_name_reads_builtin_tool_type() -> None:
    client = GrokClient(api_key="test-key")
    tool_name = client._extract_tool_name({"type": "x_search"})
    assert tool_name == "x_search"
