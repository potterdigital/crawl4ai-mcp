"""Unit tests for deep_crawl MCP tool.

Tests cover:
- deep_crawl is registered as an MCP tool
- BFSDeepCrawlStrategy, FilterChain, URLPatternFilter can be imported
- FilterChain construction with include patterns
- FilterChain construction with exclude patterns (reverse=True)
- FilterChain construction with no filters (empty)
- Scope-to-include_external mapping logic
"""

import pytest

from crawl4ai_mcp.server import deep_crawl, mcp


# ---------------------------------------------------------------------------
# Tool Registration
# ---------------------------------------------------------------------------


class TestDeepCrawlRegistration:
    def test_deep_crawl_tool_registered(self) -> None:
        """deep_crawl is registered in the FastMCP tool manager."""
        tool_names = list(mcp._tool_manager._tools.keys())
        assert "deep_crawl" in tool_names

    def test_docstring_documents_bfs_behavior(self) -> None:
        """Docstring explains BFS link-following, depth, and page limits."""
        doc = deep_crawl.__doc__
        assert doc is not None
        assert "BFS" in doc or "breadth-first" in doc.lower()
        assert "max_depth" in doc
        assert "max_pages" in doc
        assert "deduplication" in doc.lower() or "deduplic" in doc.lower()


# ---------------------------------------------------------------------------
# BFS Strategy Imports
# ---------------------------------------------------------------------------


class TestBFSStrategyImports:
    def test_bfs_strategy_imports(self) -> None:
        """BFSDeepCrawlStrategy, FilterChain, URLPatternFilter can be imported."""
        from crawl4ai.deep_crawling import (
            BFSDeepCrawlStrategy,
            FilterChain,
            URLPatternFilter,
        )

        # Verify they are classes (not None or some other object)
        assert callable(BFSDeepCrawlStrategy)
        assert callable(FilterChain)
        assert callable(URLPatternFilter)


# ---------------------------------------------------------------------------
# FilterChain Construction
# ---------------------------------------------------------------------------


class TestFilterChainConstruction:
    def test_filter_chain_construction_include(self) -> None:
        """URLPatternFilter with include pattern creates a valid filter in chain."""
        from crawl4ai.deep_crawling import FilterChain, URLPatternFilter

        url_filter = URLPatternFilter(patterns=["/docs/*"])
        chain = FilterChain(filters=[url_filter])
        assert chain is not None
        assert len(chain.filters) == 1
        assert chain.filters[0] is url_filter

    def test_filter_chain_construction_exclude(self) -> None:
        """URLPatternFilter with reverse=True creates an exclusion filter."""
        from crawl4ai.deep_crawling import URLPatternFilter

        url_filter = URLPatternFilter(patterns=["/internal/*"], reverse=True)
        assert url_filter is not None
        assert url_filter.reverse is True

    def test_filter_chain_construction_empty(self) -> None:
        """FilterChain() with no filters creates successfully."""
        from crawl4ai.deep_crawling import FilterChain

        chain = FilterChain()
        assert chain is not None


# ---------------------------------------------------------------------------
# Scope Mapping
# ---------------------------------------------------------------------------


class TestScopeMapping:
    @pytest.mark.parametrize(
        "scope,expected_include_external",
        [
            ("same-domain", False),
            ("same-origin", False),
            ("any", True),
        ],
    )
    def test_scope_mapping(self, scope: str, expected_include_external: bool) -> None:
        """Scope parameter maps correctly to include_external boolean."""
        # Reproduce the mapping logic from deep_crawl tool
        if scope in ("same-domain", "same-origin"):
            include_external = False
        elif scope == "any":
            include_external = True
        else:
            include_external = False  # default fallback

        assert include_external == expected_include_external

    def test_unknown_scope_defaults_to_same_domain(self) -> None:
        """Unknown scope value defaults to include_external=False (same-domain)."""
        scope = "unknown-scope"
        if scope in ("same-domain", "same-origin"):
            include_external = False
        elif scope == "any":
            include_external = True
        else:
            include_external = False

        assert include_external is False


# ---------------------------------------------------------------------------
# The page cap counts what upstream counts (2.5.0)
# ---------------------------------------------------------------------------


def _drive_deep_crawl(pages: list[tuple[str, bool, int]], **kwargs):
    """Run deep_crawl against a fake crawler that yields `pages` in order.

    Each page is (path, success, depth). Returns (strategy, result), where
    strategy is the object actually handed to crawl4ai. Every attribute the
    code reads is set explicitly: a MagicMock attribute left unset is truthy,
    which would pass these tests against code that misreads the real thing.
    """
    import asyncio
    from unittest.mock import MagicMock, patch

    captured: dict = {}

    def _result(path: str, success: bool, depth: int):
        page = MagicMock()
        page.url = f"https://example.com{path}"
        page.success = success
        page.status_code = 200 if success else None
        page.metadata = {"depth": depth, "parent_url": None}
        page.markdown.fit_markdown = "content" if success else ""
        page.markdown.raw_markdown = "content" if success else ""
        page.links = {}
        page.tables = []
        page.crawl_stats = None
        page.redirected_url = None
        page.response_headers = None
        page.error_message = None if success else "net::ERR_EMPTY_RESPONSE"
        return page

    async def _fake_arun(url, config, **_):
        captured["strategy"] = config.deep_crawl_strategy

        async def _stream():
            for path, success, depth in pages:
                yield _result(path, success, depth)

        return _stream()

    crawler = MagicMock()
    crawler.arun = _fake_arun
    ctx = MagicMock()
    ctx.request_context.lifespan_context = MagicMock()
    ctx.report_progress = MagicMock(side_effect=lambda *a, **k: asyncio.sleep(0))

    with patch("crawl4ai_mcp.server._require_crawler", return_value=crawler):
        out = asyncio.run(deep_crawl(url="https://example.com/", ctx=ctx, **kwargs))
    return captured["strategy"], out


class TestPageCapCountsSuccesses:
    """crawl4ai counts only successful pages toward max_pages, and yields the
    failed ones too. deep_crawl's own cap counted everything, so a failed fetch
    displaced a page that had been crawled successfully."""

    def test_a_failed_fetch_does_not_displace_a_crawled_page(self) -> None:
        """Regression: the cap sliced the depth-sorted list, failures included.

        Live on crawl4ai 0.9.5 (live_check deep-crawl, fixture /f/, bfs,
        max_pages=3), before the fix: crawled=2 total=3, keeping /f/, /f/a and
        the dead link while /f/b, crawled successfully, was thrown away.
        """
        _, out = _drive_deep_crawl(
            [("/", True, 0), ("/dead", False, 1), ("/a", True, 1), ("/b", True, 1)],
            max_pages=3,
        )
        assert out.crawled == 3
        assert out.total == 4
        dead = [p for p in out.pages if p.url.endswith("/dead")]
        assert len(dead) == 1 and dead[0].success is False

    def test_an_over_delivery_is_still_capped(self) -> None:
        """Regression: dropping the cap. It is what keeps the BFS +1 safe if
        upstream ever stops discarding the last counted page in stream mode."""
        _, out = _drive_deep_crawl(
            [("/", True, 0), ("/a", True, 1), ("/b", True, 1), ("/c", True, 2)],
            max_pages=3,
        )
        assert out.crawled == 3
        assert out.total == 3
        assert not any(p.url.endswith("/c") for p in out.pages)

    def test_bfs_asks_upstream_for_one_extra(self) -> None:
        """Regression: removing the +1 makes every BFS crawl return one page
        fewer on 0.9.5, which still drops the last counted page in stream mode
        (bfs_strategy._arun_stream breaks before the yield)."""
        strategy, _ = _drive_deep_crawl([("/", True, 0)], max_pages=3)
        assert strategy.max_pages == 4

    def test_best_first_asks_upstream_for_exactly_max_pages(self) -> None:
        """Regression: padding best-first too. It yields before it breaks, so
        it has no off-by-one and a +1 would be one page of wasted fetching."""
        strategy, _ = _drive_deep_crawl(
            [("/", True, 0)],
            max_pages=3,
            strategy="best-first",
            relevance_keywords=["install"],
        )
        assert strategy.max_pages == 3
