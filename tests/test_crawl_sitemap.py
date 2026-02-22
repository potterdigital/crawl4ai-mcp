"""Unit tests for crawl_sitemap tool registration, _fetch_sitemap_urls helper,
and SITEMAP_NS constant.

Tests cover:
- crawl_sitemap is registered as an MCP tool
- _fetch_sitemap_urls parses regular sitemaps with XML namespace
- _fetch_sitemap_urls parses sitemaps without namespace (fallback)
- _fetch_sitemap_urls recursively resolves sitemap index files
- _fetch_sitemap_urls returns empty list for empty sitemaps
- SITEMAP_NS contains the standard sitemap namespace
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from crawl4ai_mcp.server import SITEMAP_NS, _fetch_sitemap_urls, mcp


# ---------------------------------------------------------------------------
# crawl_sitemap -- tool registration
# ---------------------------------------------------------------------------


class TestCrawlSitemapRegistration:
    def test_crawl_sitemap_tool_registered(self) -> None:
        """crawl_sitemap is registered in the FastMCP tool manager."""
        tool_names = list(mcp._tool_manager._tools.keys())
        assert "crawl_sitemap" in tool_names


# ---------------------------------------------------------------------------
# _fetch_sitemap_urls -- regular sitemap with namespace
# ---------------------------------------------------------------------------


class TestFetchSitemapUrlsRegular:
    @pytest.mark.asyncio
    async def test_fetch_sitemap_urls_regular(self) -> None:
        """Parses a standard sitemap XML with namespace, extracting all <loc> URLs."""
        sitemap_xml = b"""\
<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/page1</loc></url>
  <url><loc>https://example.com/page2</loc></url>
</urlset>"""

        mock_response = MagicMock()
        mock_response.content = sitemap_xml
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("crawl4ai_mcp.server.httpx.AsyncClient", return_value=mock_client):
            urls = await _fetch_sitemap_urls("https://example.com/sitemap.xml")

        assert urls == ["https://example.com/page1", "https://example.com/page2"]


# ---------------------------------------------------------------------------
# _fetch_sitemap_urls -- sitemap without namespace
# ---------------------------------------------------------------------------


class TestFetchSitemapUrlsNoNamespace:
    @pytest.mark.asyncio
    async def test_fetch_sitemap_urls_no_namespace(self) -> None:
        """Parses a sitemap XML that omits the namespace declaration."""
        sitemap_xml = b"""\
<?xml version="1.0"?>
<urlset>
  <url><loc>https://example.com/page1</loc></url>
</urlset>"""

        mock_response = MagicMock()
        mock_response.content = sitemap_xml
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("crawl4ai_mcp.server.httpx.AsyncClient", return_value=mock_client):
            urls = await _fetch_sitemap_urls("https://example.com/sitemap.xml")

        assert urls == ["https://example.com/page1"]


# ---------------------------------------------------------------------------
# _fetch_sitemap_urls -- sitemap index (recursive)
# ---------------------------------------------------------------------------


class TestFetchSitemapUrlsIndex:
    @pytest.mark.asyncio
    async def test_fetch_sitemap_urls_index(self) -> None:
        """Recursively resolves a sitemap index file to extract leaf URLs."""
        index_xml = b"""\
<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/sitemap1.xml</loc></sitemap>
</sitemapindex>"""

        sub_sitemap_xml = b"""\
<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/from-sub-1</loc></url>
  <url><loc>https://example.com/from-sub-2</loc></url>
</urlset>"""

        def make_response(content: bytes):
            resp = MagicMock()
            resp.content = content
            resp.raise_for_status = MagicMock()
            return resp

        # First call returns index, second returns sub-sitemap
        call_count = 0

        async def mock_get(url):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return make_response(index_xml)
            return make_response(sub_sitemap_xml)

        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("crawl4ai_mcp.server.httpx.AsyncClient", return_value=mock_client):
            urls = await _fetch_sitemap_urls("https://example.com/sitemap_index.xml")

        assert urls == ["https://example.com/from-sub-1", "https://example.com/from-sub-2"]


# ---------------------------------------------------------------------------
# _fetch_sitemap_urls -- empty sitemap
# ---------------------------------------------------------------------------


class TestFetchSitemapUrlsEmpty:
    @pytest.mark.asyncio
    async def test_fetch_sitemap_urls_empty(self) -> None:
        """Returns empty list for a valid but empty sitemap (no <url> elements)."""
        sitemap_xml = b"""\
<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
</urlset>"""

        mock_response = MagicMock()
        mock_response.content = sitemap_xml
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("crawl4ai_mcp.server.httpx.AsyncClient", return_value=mock_client):
            urls = await _fetch_sitemap_urls("https://example.com/sitemap.xml")

        assert urls == []


# ---------------------------------------------------------------------------
# SITEMAP_NS -- namespace constant
# ---------------------------------------------------------------------------


class TestSitemapNsConstant:
    def test_sitemap_ns_constant(self) -> None:
        """SITEMAP_NS contains the standard sitemaps.org namespace."""
        assert "sm" in SITEMAP_NS
        assert SITEMAP_NS["sm"] == "http://www.sitemaps.org/schemas/sitemap/0.9"
