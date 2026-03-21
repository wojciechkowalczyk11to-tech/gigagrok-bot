"""Tests for utils module."""

from __future__ import annotations

from utils import (
    escape_html,
    format_footer,
    format_number,
    markdown_to_telegram_html,
    split_message,
)


# ---------------------------------------------------------------------------
# escape_html
# ---------------------------------------------------------------------------

class TestEscapeHtml:
    def test_escape_html(self) -> None:
        assert escape_html("<b>Hello</b> & 'world'") == "&lt;b&gt;Hello&lt;/b&gt; &amp; 'world'"
        assert escape_html("no special chars") == "no special chars"
        assert escape_html("") == ""


# ---------------------------------------------------------------------------
# markdown_to_telegram_html
# ---------------------------------------------------------------------------

class TestMarkdownToTelegramHtml:
    def test_markdown_to_telegram_html_bold(self) -> None:
        result = markdown_to_telegram_html("**bold text**")
        assert "<b>bold text</b>" in result

    def test_markdown_to_telegram_html_code_block(self) -> None:
        result = markdown_to_telegram_html("```python\nprint('hello')\n```")
        assert "<pre><code>" in result
        assert "print(&#x27;hello&#x27;)" in result or "print('hello')" in result

    def test_markdown_to_telegram_html_inline_code(self) -> None:
        result = markdown_to_telegram_html("Use `my_func()` here")
        assert "<code>my_func()</code>" in result

    def test_markdown_to_telegram_html_link(self) -> None:
        result = markdown_to_telegram_html("[click](https://example.com)")
        assert '<a href="https://example.com">click</a>' in result


# ---------------------------------------------------------------------------
# split_message
# ---------------------------------------------------------------------------

class TestSplitMessage:
    def test_split_message_short(self) -> None:
        text = "short message"
        assert split_message(text) == [text]

    def test_split_message_long(self) -> None:
        # Build text that exceeds default 4000 limit
        text = ("word " * 900).strip()  # ~4500 chars
        parts = split_message(text, max_length=100)
        assert len(parts) > 1
        # Reassembled text should contain all original content (minus stripped newlines)
        combined = "".join(parts)
        assert "word" in combined

    def test_split_message_preserves_code_blocks(self) -> None:
        code_block = "```python\nprint('hello world')\n```"
        padding = "x" * 3950
        text = padding + "\n\n" + code_block
        parts = split_message(text, max_length=4000)
        # Code block should be intact in one of the parts
        found = any("```python" in part and "```" in part[part.index("```python") + 3:] for part in parts)
        assert found, "Code block was split across parts"


# ---------------------------------------------------------------------------
# format_number
# ---------------------------------------------------------------------------

class TestFormatNumber:
    def test_format_number_thousands(self) -> None:
        assert format_number(1234) == "1.2K"

    def test_format_number_millions(self) -> None:
        assert format_number(1_500_000) == "1.5M"

    def test_format_number_small(self) -> None:
        assert format_number(42) == "42"

    def test_format_number_zero(self) -> None:
        assert format_number(0) == "0"


# ---------------------------------------------------------------------------
# format_footer
# ---------------------------------------------------------------------------

class TestFormatFooter:
    def test_format_footer(self) -> None:
        result = format_footer(
            model="grok-test",
            tokens_in=1000,
            tokens_out=2000,
            reasoning_tokens=500,
            cost_usd=0.0123,
            elapsed_seconds=1.5,
        )
        assert "grok-test" in result
        assert "1.0K" in result
        assert "2.0K" in result
        assert "$0.0123" in result
        assert "1.5s" in result
