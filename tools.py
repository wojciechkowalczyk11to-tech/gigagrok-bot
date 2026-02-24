"""Definitions for xAI search and tool configurations."""

from __future__ import annotations
from typing import Any

# Search parameter for built-in web search
SEARCH_WEB = {"search": {"enabled": True}}

# Agent tools (require client-side handling)
TOOL_CODE_EXEC: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "code_execution",
        "description": "Execute code in a sandboxed environment.",
        "parameters": {},
    },
}

TOOLS_ALL: list[dict[str, Any]] = [TOOL_CODE_EXEC]
