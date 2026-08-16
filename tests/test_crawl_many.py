"""Unit tests for crawl_many registration and the structured batch result.

The batch crawl tools return a CrawlBatchResult model rather than a formatted
string, so the SDK derives an outputSchema and clients get real data in
`structuredContent` instead of prose they would have to parse.

Each test below pins one property that a caller depends on:
- a partial crawl never discards the pages that worked
- failures carry their reason, so a caller can tell a timeout from a 404
- deep_crawl's depth and parent metadata survive into the model
- successes sort ahead of failures, so the useful half is not buried
- deep_crawl_strategy still reaches build_run_config
"""

from unittest.mock import MagicMock

from crawl4ai_mcp.profiles import _PER_CALL_KEYS
from crawl4ai_mcp.server import CrawlBatchResult, _batch_result, mcp


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
# crawl_many — tool registration and declared output shape
# ---------------------------------------------------------------------------


class TestCrawlManyRegistration:
    def test_crawl_many_tool_registered(self) -> None:
        """crawl_many is registered in the tool manager."""
        assert "crawl_many" in list(mcp._tool_manager._tools.keys())


# ---------------------------------------------------------------------------
# _batch_result — success cases
# ---------------------------------------------------------------------------


class TestBatchResultSuccess:
    def test_all_successes(self) -> None:
        """Counts and content survive into the model."""
        results = [
            _make_result("https://example.com/page1", content="Page 1 content"),
            _make_result("https://example.com/page2", content="Page 2 content"),
        ]
        out = _batch_result(results)

        assert isinstance(out, CrawlBatchResult)
        assert (out.crawled, out.total) == (2, 2)
        assert [p.url for p in out.pages] == [
            "https://example.com/page1",
            "https://example.com/page2",
        ]
        assert [p.markdown for p in out.pages] == ["Page 1 content", "Page 2 content"]
        assert all(p.success for p in out.pages)
        assert all(p.error is None for p in out.pages)


# ---------------------------------------------------------------------------
# _batch_result — mixed success/failure
# ---------------------------------------------------------------------------


class TestBatchResultMixed:
    def test_partial_crawl_keeps_successes_and_failures(self) -> None:
        """A failing URL must never discard the pages that worked."""
        results = [
            _make_result("https://example.com/good", content="Good content"),
            _make_result("https://example.com/bad", success=False,
                         error_message="Connection timeout"),
        ]
        out = _batch_result(results)

        assert (out.crawled, out.total) == (1, 2)

        good = next(p for p in out.pages if p.url.endswith("/good"))
        assert good.success and good.markdown == "Good content"

        bad = next(p for p in out.pages if p.url.endswith("/bad"))
        assert not bad.success
        assert bad.error == "Connection timeout"
        assert bad.markdown is None

    def test_successes_sort_before_failures(self) -> None:
        """Otherwise a mostly-failed crawl buries the pages the caller wanted."""
        results = [
            _make_result("https://example.com/bad1", success=False, error_message="x"),
            _make_result("https://example.com/good", content="c"),
            _make_result("https://example.com/bad2", success=False, error_message="y"),
        ]
        out = _batch_result(results)

        assert [p.success for p in out.pages] == [True, False, False]


# ---------------------------------------------------------------------------
# _batch_result — all failures
# ---------------------------------------------------------------------------


class TestBatchResultAllFailures:
    def test_all_failures_report_every_reason(self) -> None:
        """A caller distinguishing DNS from TLS needs the per-URL reason."""
        results = [
            _make_result("https://example.com/fail1", success=False,
                         error_message="DNS resolution failed"),
            _make_result("https://example.com/fail2", success=False,
                         error_message="SSL handshake error"),
        ]
        out = _batch_result(results)

        assert (out.crawled, out.total) == (0, 2)
        assert {p.error for p in out.pages} == {
            "DNS resolution failed",
            "SSL handshake error",
        }


# ---------------------------------------------------------------------------
# _batch_result — deep_crawl metadata
# ---------------------------------------------------------------------------


class TestBatchResultDepthMetadata:
    def test_depth_and_parent_survive(self) -> None:
        """deep_crawl's tree shape is only reconstructable if these carry through."""
        results = [
            _make_result("https://example.com/root", content="Root page",
                         metadata={"depth": 0}),
            _make_result("https://example.com/child", content="Child page",
                         metadata={"depth": 1, "parent_url": "https://example.com/root"}),
        ]
        out = _batch_result(results)

        root = next(p for p in out.pages if p.url.endswith("/root"))
        child = next(p for p in out.pages if p.url.endswith("/child"))
        assert root.depth == 0 and root.parent_url is None
        assert child.depth == 1
        assert child.parent_url == "https://example.com/root"

    def test_note_is_carried_when_given(self) -> None:
        """crawl_sitemap reports truncation through this field."""
        out = _batch_result([_make_result("https://example.com/a")], note="truncated")
        assert out.note == "truncated"

    def test_note_is_none_by_default(self) -> None:
        out = _batch_result([_make_result("https://example.com/a")])
        assert out.note is None


# ---------------------------------------------------------------------------
# _PER_CALL_KEYS — deep_crawl_strategy
# ---------------------------------------------------------------------------


class TestPerCallKeys:
    def test_deep_crawl_strategy_in_per_call_keys(self) -> None:
        """deep_crawl_strategy is in _PER_CALL_KEYS so it passes through build_run_config."""
        assert "deep_crawl_strategy" in _PER_CALL_KEYS
