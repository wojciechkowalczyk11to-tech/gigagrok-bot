"""Server-side tool definitions for xAI /responses and /chat/completions API."""

from __future__ import annotations
from typing import Any


# ---------------------------------------------------------------------------
# Server-side tools (for /responses or tool-augmented /chat/completions)
# ---------------------------------------------------------------------------

def tool_file_search(collection_id: str) -> dict[str, Any]:
    """Return a file_search tool definition bound to a vector store."""
    return {
        "type": "file_search",
        "vector_store_ids": [collection_id],
        "max_num_results": 10,
    }


TOOL_WEB: dict[str, Any] = {"type": "web_search"}
TOOL_X: dict[str, Any] = {"type": "x_search"}
TOOL_CODE: dict[str, Any] = {"type": "code_interpreter"}


def build_stage1_tools(collection_id: str) -> list[dict[str, Any]]:
    """Stage 1: collection search only."""
    return [tool_file_search(collection_id)]


def build_stage2_tools() -> list[dict[str, Any]]:
    """Stage 2: web, X, and code tools."""
    return [TOOL_WEB, TOOL_X, TOOL_CODE]
