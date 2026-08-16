"""Unit tests for extract_css tool registration, docstring, and EXTR-03 enforcement.

Tests cover:
- extract_css is registered as an MCP tool
- extract_css docstring states "no LLM" and "no cost"
- extract_css has no "provider" parameter (no LLM config leaks into CSS tool)
- crawl_url has no extraction_strategy or schema parameters (EXTR-03)
- extract_css, extract_structured, and crawl_url are separate function objects
"""

import inspect

from crawl4ai_mcp.server import (
    crawl_url,
    extract_css,
    extract_structured,
    mcp,
)


# ---------------------------------------------------------------------------
# extract_css — tool registration and docstring
# ---------------------------------------------------------------------------


class TestExtractCssRegistration:
    def test_tool_registered(self) -> None:
        """extract_css is registered in the FastMCP tool manager."""
        tool_names = list(mcp._tool_manager._tools.keys())
        assert "extract_css" in tool_names

    def test_docstring_no_llm_no_cost(self) -> None:
        """Docstring clearly states no LLM and no cost."""
        doc = extract_css.__doc__
        assert doc is not None
        assert "no LLM" in doc
        assert "no cost" in doc

    def test_no_provider_parameter(self) -> None:
        """extract_css has no 'provider' parameter — no LLM config leaks."""
        sig = inspect.signature(extract_css)
        assert "provider" not in sig.parameters


# ---------------------------------------------------------------------------
# EXTR-03 — crawl_url never triggers extraction
# ---------------------------------------------------------------------------


class TestExtr03Enforcement:
    def test_crawl_url_has_no_extraction_strategy_param(self) -> None:
        """crawl_url does not accept an extraction_strategy parameter."""
        sig = inspect.signature(crawl_url)
        assert "extraction_strategy" not in sig.parameters

    def test_crawl_url_has_no_schema_param(self) -> None:
        """crawl_url does not accept a schema parameter."""
        sig = inspect.signature(crawl_url)
        assert "schema" not in sig.parameters

    def test_extraction_tools_are_separate_functions(self) -> None:
        """extract_css, extract_structured, and crawl_url are distinct functions."""
        assert crawl_url is not extract_css
        assert extract_structured is not extract_css
        assert crawl_url is not extract_structured


class TestExtractPatternsInputFormat:
    """extract_patterns must not use crawl4ai's default input_format.

    fit_html is the content-FILTERED html, and this tool builds a bare config
    with no content filter, so fit_html is nearly empty. Measured on a real
    page (python.org/about/help): fit_html found 3 emails and 0 urls, html
    found 6 emails and 77 urls. The default silently under-reports instead of
    failing, which is the worst shape for an extraction tool.
    """

    def test_builds_the_strategy_with_html_not_fit_html(self) -> None:
        import inspect

        from crawl4ai_mcp.server import extract_patterns

        src = inspect.getsource(extract_patterns)
        assert 'input_format="html"' in src, (
            "extract_patterns must pass input_format explicitly; crawl4ai's "
            "fit_html default returns almost nothing here"
        )

    def test_rejects_unknown_pattern_names_with_the_valid_list(self) -> None:
        """A typo'd pattern name should say what the options are, not return
        an empty result that looks like 'this page has no emails'."""
        import asyncio
        from unittest.mock import MagicMock

        from crawl4ai_mcp.server import extract_patterns

        ctx = MagicMock()
        ctx.request_context.lifespan_context = MagicMock()
        out = asyncio.run(
            extract_patterns(url="https://example.com", patterns=["nope"], ctx=ctx)
        )
        assert out.count == 0
        assert "Unknown pattern name" in out.error
        assert "email" in out.error

    def test_requesting_nothing_is_an_explicit_error(self) -> None:
        import asyncio
        from unittest.mock import MagicMock

        from crawl4ai_mcp.server import extract_patterns

        ctx = MagicMock()
        ctx.request_context.lifespan_context = MagicMock()
        out = asyncio.run(
            extract_patterns(url="https://example.com", patterns=[], ctx=ctx)
        )
        assert out.count == 0
        assert "No patterns requested" in out.error
