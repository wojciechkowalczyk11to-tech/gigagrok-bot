"""Definitions for xAI Agent Tools used by command handlers."""

from __future__ import annotations

from typing import Any

TOOL_WEB_SEARCH: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the internet for current information, news, facts, documentation",
    },
}

TOOL_X_SEARCH: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "x_search",
        "description": "Search posts and discussions on X (Twitter)",
    },
}

TOOL_CODE_EXEC: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "code_execution",
        "description": "Execute code in a sandboxed environment. Supports Python, JavaScript, and more.",
    },
}

TOOLS_ALL: list[dict[str, Any]] = [TOOL_WEB_SEARCH, TOOL_X_SEARCH, TOOL_CODE_EXEC]


def get_tools(command: str) -> list[dict[str, Any]]:
    """Zwróć tools dla danej komendy."""
    mapping: dict[str, list[dict[str, Any]]] = {
        "websearch": [TOOL_WEB_SEARCH],
        "xsearch": [TOOL_X_SEARCH],
        "code": [TOOL_CODE_EXEC],
        "analyze": [TOOL_WEB_SEARCH, TOOL_CODE_EXEC],
        "gigagrok": TOOLS_ALL,
    }
    return mapping.get(command, [])
