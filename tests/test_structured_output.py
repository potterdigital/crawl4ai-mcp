"""Tests for the structured results the crawl and extract tools return.

These four tools return Pydantic models rather than formatted strings, so the
SDK derives an outputSchema and clients receive real data in
`structuredContent` instead of prose they would have to parse back.

The failure modes pinned here:

- a tool silently loses its declared output schema, so clients go back to
  parsing text and cannot validate anything
- extract_css hands back its JSON as a *string* inside the JSON result,
  making the caller parse twice
- an extraction failure raises out of the tool instead of being reported,
  which loses the URL and the reason
- malformed or unexpected extractor output crashes the tool rather than
  being surfaced
- crawl_url gets converted too, burying page markdown in JSON escaping for
  no benefit
"""

import asyncio
import inspect
import json
import textwrap
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from crawl4ai_mcp import server as srv
from crawl4ai_mcp.server import CrawlBatchResult, ExtractionResult, extract_css

STRUCTURED_TOOLS = ["crawl_many", "crawl_sitemap", "deep_crawl", "extract_css"]


@pytest.fixture(scope="module")
def tools() -> list:
    return asyncio.run(srv.mcp.list_tools())


def _schema(tools, name) -> dict | None:
    return next(t for t in tools if t.name == name).output_schema


class TestDeclaredSchemas:
    @pytest.mark.parametrize("name", STRUCTURED_TOOLS)
    def test_tool_declares_a_real_output_schema(self, tools, name: str) -> None:
        """A str-returning tool gets a trivial {"result": str} wrapper schema.
        Seeing that here means the model return type was lost."""
        schema = _schema(tools, name)
        assert schema is not None, f"{name} has no outputSchema"
        assert list(schema.get("properties", {})) != ["result"], (
            f"{name} fell back to the string wrapper schema"
        )

    def test_batch_tools_share_one_shape(self, tools) -> None:
        """Three tools crawl many pages; a caller should not need three parsers."""
        shapes = {
            name: sorted(_schema(tools, name)["properties"])
            for name in ("crawl_many", "crawl_sitemap", "deep_crawl")
        }
        assert len(set(map(tuple, shapes.values()))) == 1, shapes

    def test_crawl_url_still_returns_plain_markdown(self, tools) -> None:
        """Converting it would bury page content in JSON escaping for no gain:
        a single page has no tabular structure to expose."""
        schema = _schema(tools, "crawl_url")
        assert list(schema["properties"]) == ["result"]


def _extraction_ctx():
    """Minimal Context stand-in: extract_css only reaches the lifespan context."""
    ctx = MagicMock()
    ctx.request_context.lifespan_context = MagicMock()
    return ctx


def _run_extract_css(crawl_result):
    """Invoke extract_css with the crawler stubbed out."""
    with (
        patch.object(srv, "_require_crawler", return_value=MagicMock()),
        patch.object(
            srv, "_crawl_with_overrides", AsyncMock(return_value=crawl_result)
        ),
    ):
        return asyncio.run(
            extract_css(
                url="https://example.com",
                schema={"name": "X", "baseSelector": "div", "fields": []},
                ctx=_extraction_ctx(),
            )
        )


def _crawl_result(success=True, extracted=None, error_message="", status_code=200):
    r = MagicMock()
    r.success = success
    r.extracted_content = extracted
    r.error_message = error_message
    r.status_code = status_code
    return r


class TestExtractCssStructured:
    def test_items_come_back_parsed_not_as_a_json_string(self) -> None:
        """crawl4ai hands back a JSON string. If that string is passed straight
        through, the caller has to parse JSON out of JSON."""
        payload = [{"title": "A", "price": "1"}, {"title": "B", "price": "2"}]

        out = _run_extract_css(_crawl_result(extracted=json.dumps(payload)))

        assert isinstance(out, ExtractionResult)
        assert out.items == payload
        assert out.count == 2
        assert out.error is None
        assert out.url == "https://example.com"

    def test_a_single_object_becomes_a_one_item_list(self) -> None:
        """items is declared as a list; a bare object must not break the schema."""
        out = _run_extract_css(_crawl_result(extracted='{"title": "solo"}'))

        assert out.items == [{"title": "solo"}]
        assert out.count == 1

    def test_a_failed_crawl_is_reported_not_raised(self) -> None:
        """Raising would lose the URL and the reason the caller needs."""
        out = _run_extract_css(
            _crawl_result(success=False, error_message="Connection refused")
        )

        assert out.count == 0 and out.items == []
        assert out.error is not None
        assert "Connection refused" in out.error

    def test_no_matches_explains_itself(self) -> None:
        """An empty result is almost always a wrong selector; say so."""
        out = _run_extract_css(_crawl_result(extracted="[]"))

        assert out.count == 0 and out.items == []
        assert "baseSelector" in out.error

    def test_malformed_json_is_surfaced_not_crashed(self) -> None:
        """A crash here would surface as an opaque tool error."""
        out = _run_extract_css(_crawl_result(extracted="{not json"))

        assert out.count == 0 and out.items == []
        assert "malformed JSON" in out.error

    def test_unexpected_top_level_type_is_surfaced(self) -> None:
        """A bare scalar is neither a record nor a list of them."""
        out = _run_extract_css(_crawl_result(extracted='"just a string"'))

        assert out.count == 0 and out.items == []
        assert "expected a list of records" in out.error


class TestHttpErrorPagesAreDetectable:
    """crawl4ai reports success for any page it managed to fetch, so an HTTP
    404 arrives with success=True and the error page's body as markdown.
    Verified against a real 404 (docs.astral.sh returns a genuine 404 status).

    Without status_code on the model a caller cannot tell that page apart from
    real content, and `pages.filter(p => p.success)` silently ingests error
    pages. This is sharper now than it was with the old prose output, because
    success is a typed field clients will branch on.
    """

    def test_status_code_is_carried_for_successful_fetches(self) -> None:
        result = MagicMock()
        result.url = "https://example.com/missing"
        result.success = True
        result.status_code = 404
        result.error_message = ""
        result.metadata = {}
        result.markdown.fit_markdown = "Page not found"
        result.markdown.raw_markdown = "Page not found"

        out = srv._batch_result([result])

        assert out.pages[0].success is True
        assert out.pages[0].status_code == 404, (
            "a 404 is indistinguishable from real content without this"
        )

    def test_status_code_is_carried_for_failed_fetches(self) -> None:
        result = MagicMock()
        result.url = "https://example.com/blocked"
        result.success = False
        result.status_code = 403
        result.error_message = "Blocked"
        result.metadata = {}
        result.markdown = None

        out = srv._batch_result([result])

        assert out.pages[0].status_code == 403

    def test_status_code_is_in_the_declared_schema(self, tools) -> None:
        """A client that validates against the schema must be allowed to read it."""
        schema = _schema(tools, "crawl_many")
        page_props = schema["$defs"]["PageResult"]["properties"]
        assert "status_code" in page_props


class TestEveryReturnPathMatchesTheDeclaredType:
    """The bug this exists for: crawl_sitemap's two early-return error paths
    still returned strings after the tool was annotated -> CrawlBatchResult.
    Pydantic rejected them at the boundary, so an unreachable or non-sitemap
    URL produced an opaque "1 validation error for CrawlBatchResult" crash
    instead of the reason it failed. Found by pointing the tool at a real
    sitemap index and at an HTML page; no existing test touched those paths.

    This walks the AST instead of calling the tools, so it covers error
    branches that need a specific network failure to reach.
    """

    RETURN_TYPES = {
        "crawl_many": "CrawlBatchResult",
        "crawl_sitemap": "CrawlBatchResult",
        "deep_crawl": "CrawlBatchResult",
        "extract_css": "ExtractionResult",
    }
    HELPERS = {"_batch_result", "_persist_results"}

    @pytest.mark.parametrize("tool_name", sorted(RETURN_TYPES))
    def test_no_return_path_yields_a_bare_string(self, tool_name: str) -> None:
        import ast

        want = self.RETURN_TYPES[tool_name]
        source = textwrap.dedent(inspect.getsource(getattr(srv, tool_name)))
        fn = ast.parse(source).body[0]

        offenders = []
        for node in ast.walk(fn):
            if not isinstance(node, ast.Return) or node.value is None:
                continue
            value = node.value
            ok = isinstance(value, ast.Call) and (
                getattr(value.func, "id", "") == want
                or getattr(value.func, "id", "") in self.HELPERS
            )
            if not ok:
                offenders.append(ast.unparse(value)[:70])

        assert offenders == [], (
            f"{tool_name} declares -> {want} but returns something else; "
            f"these fail validation at the tool boundary: {offenders}"
        )


class TestSitemapFailuresAreReportable:
    def test_unfetchable_sitemap_reports_instead_of_crashing(self) -> None:
        """A real sitemap URL that 404s or times out must come back as data."""
        with patch.object(
            srv, "_fetch_sitemap_urls", AsyncMock(side_effect=httpx.ConnectError("boom"))
        ):
            out = asyncio.run(
                srv.crawl_sitemap(sitemap_url="https://example.com/sitemap.xml",
                                  ctx=_extraction_ctx())
            )

        assert isinstance(out, CrawlBatchResult)
        assert (out.crawled, out.total, out.pages) == (0, 0, [])
        assert "boom" in out.error

    def test_a_parse_failure_is_not_reported_as_a_fetch_failure(self) -> None:
        """Passing an HTML page as the sitemap URL is the common mistake. Calling
        that a fetch failure sends the user to check the network, when the
        request actually succeeded and the XML parse is what failed."""
        import xml.etree.ElementTree as ET

        with patch.object(
            srv,
            "_fetch_sitemap_urls",
            AsyncMock(side_effect=ET.ParseError("syntax error: line 1, column 0")),
        ):
            out = asyncio.run(
                srv.crawl_sitemap(sitemap_url="https://example.com/",
                                  ctx=_extraction_ctx())
            )

        assert (out.crawled, out.total) == (0, 0)
        assert "not valid sitemap XML" in out.error
        assert "could not fetch" not in out.error.lower()
        assert "robots.txt" in out.error, "should say how to find the real sitemap"

    def test_a_non_sitemap_url_reports_instead_of_crashing(self) -> None:
        """Pointing the tool at an HTML page is an easy mistake to make."""
        with patch.object(srv, "_fetch_sitemap_urls", AsyncMock(return_value=[])):
            out = asyncio.run(
                srv.crawl_sitemap(sitemap_url="https://example.com/",
                                  ctx=_extraction_ctx())
            )

        assert (out.crawled, out.total) == (0, 0)
        assert "No URLs found" in out.error

    def test_a_healthy_crawl_leaves_error_unset(self) -> None:
        """error means the whole operation failed; a partial crawl must not set it."""
        page = srv.PageResult(url="https://a", success=True)
        assert CrawlBatchResult(crawled=1, total=1, pages=[page]).error is None


class TestBatchResultRoundTrips:
    def test_the_model_serialises_to_the_declared_schema(self) -> None:
        """structuredContent is this dump; if it drifts from the schema, clients
        that validate will reject perfectly good results."""
        page = srv.PageResult(url="https://a", success=True, markdown="# hi")
        batch = CrawlBatchResult(crawled=1, total=1, pages=[page])

        dumped = batch.model_dump()

        assert dumped["crawled"] == 1
        assert dumped["pages"][0]["markdown"] == "# hi"
        assert CrawlBatchResult.model_validate(dumped) == batch
