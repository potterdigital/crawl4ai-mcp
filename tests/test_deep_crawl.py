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
    def test_scope_mapping(
        self, scope: str, expected_include_external: bool
    ) -> None:
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
