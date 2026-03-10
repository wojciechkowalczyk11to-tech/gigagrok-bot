from __future__ import annotations

import pytest

from handlers.search import _build_search_tools


def test_build_search_tools_for_web() -> None:
    assert _build_search_tools("web") == [{"type": "web_search"}]


def test_build_search_tools_for_x() -> None:
    assert _build_search_tools("x") == [{"type": "x_search"}]


def test_build_search_tools_rejects_unknown_type() -> None:
    with pytest.raises(ValueError):
        _build_search_tools("unknown")
