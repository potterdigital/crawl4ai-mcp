"""Unit tests for crawl_many tool registration, _format_multi_results helper, and
_PER_CALL_KEYS update for deep_crawl_strategy.

Tests cover:
- crawl_many is registered as an MCP tool
- _format_multi_results formats success-only results correctly
- _format_multi_results formats mixed success/failure results (never discards successes)
- _format_multi_results formats all-failure results correctly
- _format_multi_results includes depth metadata when present (for deep_crawl reuse)
- deep_crawl_strategy is in _PER_CALL_KEYS
"""

from unittest.mock import MagicMock

from crawl4ai_mcp.profiles import _PER_CALL_KEYS
from crawl4ai_mcp.server import _format_multi_results, mcp


def _make_result(url: str, success: bool = True, content: str = "page content",
                 error_message: str = "", metadata: dict | None = None):
    """Create a mock CrawlResult for testing."""
    result = MagicMock()
    result.url = url
    result.success = success
    result.error_message = error_message
    result.metadata = metadata or {}

    if success:
        result.markdown.fit_markdown = content
        result.markdown.raw_markdown = content
    else:
        result.markdown = None

    return result


# ---------------------------------------------------------------------------
# crawl_many — tool registration
# ---------------------------------------------------------------------------


class TestCrawlManyRegistration:
    def test_crawl_many_tool_registered(self) -> None:
        """crawl_many is registered in the FastMCP tool manager."""
        tool_names = list(mcp._tool_manager._tools.keys())
        assert "crawl_many" in tool_names


# ---------------------------------------------------------------------------
# _format_multi_results — success cases
# ---------------------------------------------------------------------------


class TestFormatMultiResultsSuccess:
    def test_format_multi_results_success(self) -> None:
        """Formats successful results with summary line, URL headers, and content."""
        results = [
            _make_result("https://example.com/page1", content="Page 1 content"),
            _make_result("https://example.com/page2", content="Page 2 content"),
        ]
        output = _format_multi_results(results)

        assert "Crawled 2 of 2 URLs successfully." in output
        assert "## https://example.com/page1" in output
        assert "## https://example.com/page2" in output
        assert "Page 1 content" in output
        assert "Page 2 content" in output
        assert "Failed URLs" not in output


# ---------------------------------------------------------------------------
# _format_multi_results — mixed success/failure
# ---------------------------------------------------------------------------


class TestFormatMultiResultsMixed:
    def test_format_multi_results_mixed(self) -> None:
        """Formats mixed results with BOTH successes and failures — never discards successes."""
        results = [
            _make_result("https://example.com/good", content="Good content"),
            _make_result("https://example.com/bad", success=False,
                         error_message="Connection timeout"),
        ]
        output = _format_multi_results(results)

        assert "Crawled 1 of 2 URLs successfully." in output
        # Success is present
        assert "## https://example.com/good" in output
        assert "Good content" in output
        # Failure is present
        assert "## Failed URLs (1)" in output
        assert "https://example.com/bad: Connection timeout" in output


# ---------------------------------------------------------------------------
# _format_multi_results — all failures
# ---------------------------------------------------------------------------


class TestFormatMultiResultsAllFailures:
    def test_format_multi_results_all_failures(self) -> None:
        """Formats all-failure results with 0 of N summary and failure section."""
        results = [
            _make_result("https://example.com/fail1", success=False,
                         error_message="DNS resolution failed"),
            _make_result("https://example.com/fail2", success=False,
                         error_message="SSL handshake error"),
        ]
        output = _format_multi_results(results)

        assert "Crawled 0 of 2 URLs successfully." in output
        assert "## Failed URLs (2)" in output
        assert "https://example.com/fail1: DNS resolution failed" in output
        assert "https://example.com/fail2: SSL handshake error" in output


# ---------------------------------------------------------------------------
# _format_multi_results — depth metadata (deep_crawl reuse)
# ---------------------------------------------------------------------------


class TestFormatMultiResultsDepthMetadata:
    def test_format_multi_results_depth_metadata(self) -> None:
        """Includes depth info in URL header when result.metadata contains 'depth'."""
        results = [
            _make_result("https://example.com/root", content="Root page",
                         metadata={"depth": 0}),
            _make_result("https://example.com/child", content="Child page",
                         metadata={"depth": 1, "parent_url": "https://example.com/root"}),
        ]
        output = _format_multi_results(results)

        assert "## https://example.com/root (depth: 0)" in output
        assert "## https://example.com/child (depth: 1)" in output


# ---------------------------------------------------------------------------
# _PER_CALL_KEYS — deep_crawl_strategy
# ---------------------------------------------------------------------------


class TestPerCallKeys:
    def test_deep_crawl_strategy_in_per_call_keys(self) -> None:
        """deep_crawl_strategy is in _PER_CALL_KEYS so it passes through build_run_config."""
        assert "deep_crawl_strategy" in _PER_CALL_KEYS
