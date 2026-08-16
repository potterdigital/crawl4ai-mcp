"""Tests for the opt-in links and tables on multi-page crawl results.

crawl4ai populates `links` and `tables` on every CrawlResult and this server
discarded both. They are now returned when asked for, and the two properties
worth pinning pull in opposite directions:

- they must stay OFF unless requested, because they routinely outweigh the page
  content (997 internal links, ~130KB of JSON, on one Wikipedia article), and
- when requested, nothing may be quietly dropped or truncated.

The fixtures reproduce crawl4ai 0.9.2's real shapes, including the seven
per-link fields this server projects away and the table `metadata` block.
"""

from unittest.mock import MagicMock

from crawl4ai_mcp.server import (
    PageLinks,
    _batch_result,
    _page_links,
    _page_tables,
    _persist_results,
)


# One link exactly as crawl4ai 0.9.2 emits it, recorded from en.wikipedia.org.
RAW_LINK = {
    "href": "https://en.wikipedia.org/wiki/Python",
    "text": "Python",
    "title": "",
    "base_domain": "wikipedia.org",
    "head_data": None,
    "head_extraction_status": None,
    "head_extraction_error": None,
    "intrinsic_score": 0.0,
    "contextual_score": None,
    "total_score": None,
}

RAW_LINKS = {
    "internal": [RAW_LINK],
    "external": [
        {
            "href": "https://github.com/astral-sh/uv",
            "text": "uv",
            "title": "Go to repository",
            "base_domain": "github.com",
            "head_data": None,
            "intrinsic_score": 0.0,
        }
    ],
}

# One table as crawl4ai 0.9.2 emits it, recorded from en.wikipedia.org.
RAW_TABLE = {
    "headers": ["Country", "Population"],
    "rows": [["India", "1,428,627,663"], ["China", "1,425,671,352"]],
    "caption": "List of countries by population",
    "summary": "",
    "metadata": {
        "row_count": 238,
        "column_count": 6,
        "has_headers": True,
        "id": "mwJA",
        "class": "wikitable sortable mw-datatable sticky-header",
    },
}


def _result(
    url: str = "https://example.com",
    success: bool = True,
    links: object = None,
    tables: object = None,
):
    result = MagicMock()
    result.url = url
    result.success = success
    result.status_code = 200 if success else None
    result.error_message = "" if success else "boom"
    result.metadata = {}
    result.links = links
    result.tables = tables
    result.crawl_stats = None
    result.redirected_url = None
    result.response_headers = None
    if success:
        result.markdown.fit_markdown = "content"
        result.markdown.raw_markdown = "content"
    else:
        result.markdown = None
    return result


class TestOffByDefault:
    """The payload must not grow for callers who did not ask for it.

    Turning either of these on unconditionally would add hundreds of kilobytes
    to a batch crawl that only wanted the markdown.
    """

    def test_every_tool_defaults_both_flags_off(self) -> None:
        """Pinned at the tool signature, which is what the MCP schema exposes.

        The helpers below take the same defaults, but a caller never reaches
        them directly: flipping a default on crawl_many is what would actually
        ship a payload nobody asked for, and the helper-level tests cannot see
        that because the tools pass their own values through explicitly.
        """
        import inspect

        from crawl4ai_mcp.server import crawl_many, crawl_sitemap, deep_crawl

        for tool in (crawl_many, crawl_sitemap, deep_crawl):
            params = inspect.signature(tool).parameters
            assert params["include_links"].default is False, tool.__name__
            assert params["include_tables"].default is False, tool.__name__

    def test_batch_result_defaults_both_flags_off(self) -> None:
        """The shared builder every crawl tool funnels through."""
        import inspect

        params = inspect.signature(_batch_result).parameters
        assert params["include_links"].default is False
        assert params["include_tables"].default is False

    def test_links_absent_unless_requested(self) -> None:
        page = _batch_result([_result(links=RAW_LINKS)]).pages[0]
        assert page.links is None

    def test_tables_absent_unless_requested(self) -> None:
        page = _batch_result([_result(tables=[RAW_TABLE])]).pages[0]
        assert page.tables is None

    def test_each_flag_is_independent(self) -> None:
        """Asking for tables must not drag the far larger link list along."""
        page = _batch_result(
            [_result(links=RAW_LINKS, tables=[RAW_TABLE])], include_tables=True
        ).pages[0]
        assert page.tables is not None
        assert page.links is None


class TestLinksProjection:
    def test_internal_and_external_split_survives(self) -> None:
        page = _batch_result([_result(links=RAW_LINKS)], include_links=True).pages[0]
        assert isinstance(page.links, PageLinks)
        assert [link.href for link in page.links.internal] == [
            "https://en.wikipedia.org/wiki/Python"
        ]
        assert [link.href for link in page.links.external] == [
            "https://github.com/astral-sh/uv"
        ]

    def test_noise_fields_are_dropped(self) -> None:
        """The seven fields crawl4ai attaches are None or 0.0 here.

        Nothing in this server enables link-head extraction or a URL scorer, so
        head_data, the extraction status pair and the three scores are dead
        weight. Measured: keeping them takes one Wikipedia page's links from
        132KB to 317KB.
        """
        link = _page_links(RAW_LINKS).internal[0]
        dumped = link.model_dump()
        assert set(dumped) == {"href", "text", "title"}
        for dropped in (
            "base_domain",
            "head_data",
            "head_extraction_status",
            "intrinsic_score",
            "total_score",
        ):
            assert dropped not in dumped

    def test_empty_text_and_title_become_none(self) -> None:
        """crawl4ai writes "" rather than omitting; "" costs tokens and says nothing."""
        link = _page_links(RAW_LINKS).internal[0]
        assert link.text == "Python"
        assert link.title is None

    def test_link_without_href_is_skipped(self) -> None:
        """An href-less entry cannot be followed or fetched; it is not a link."""
        links = _page_links({"internal": [{"text": "nowhere"}, RAW_LINK]})
        assert len(links.internal) == 1

    def test_missing_or_malformed_links_do_not_raise(self) -> None:
        """A hard failure has links={} and upstream could change the shape."""
        for raw in (None, {}, [], {"internal": None}, {"internal": ["not-a-dict"]}):
            out = _page_links(raw)
            assert out.internal == []
            assert out.external == []

    def test_nothing_is_truncated(self) -> None:
        """The size warning is in the docstring; a silent cap would be worse."""
        many = {
            "internal": [dict(RAW_LINK, href=f"https://x.test/{i}") for i in range(750)]
        }
        assert len(_page_links(many).internal) == 750


class TestTablesProjection:
    def test_headers_rows_and_caption_survive(self) -> None:
        table = _page_tables([RAW_TABLE])[0]
        assert table.headers == ["Country", "Population"]
        assert table.rows[0] == ["India", "1,428,627,663"]
        assert table.caption == "List of countries by population"

    def test_metadata_block_is_dropped(self) -> None:
        """row_count and column_count are len() of what is already returned.

        The rest of the block is the table's raw id and class attributes,
        which tell a caller nothing about the data.
        """
        assert set(_page_tables([RAW_TABLE])[0].model_dump()) == {
            "headers",
            "rows",
            "caption",
        }

    def test_headerless_table_is_kept_not_discarded(self) -> None:
        """Plenty of real tables have no <th> row; the rows are still the data."""
        table = _page_tables([{"rows": [["a", "b"]]}])[0]
        assert table.headers == []
        assert table.rows == [["a", "b"]]
        assert table.caption is None

    def test_no_tables_yields_an_empty_list_not_none(self) -> None:
        """None would read as "not requested"; [] says "asked, found none"."""
        page = _batch_result([_result(tables=[])], include_tables=True).pages[0]
        assert page.tables == []

    def test_malformed_tables_do_not_raise(self) -> None:
        for raw in (None, {}, "nope", ["not-a-dict"]):
            assert _page_tables(raw) == []

    def test_large_table_is_not_truncated(self) -> None:
        big = {"headers": ["n"], "rows": [[str(i)] for i in range(300)]}
        assert len(_page_tables([big])[0].rows) == 300


class TestFailedPages:
    def test_failure_carries_no_links_or_tables(self) -> None:
        """A page that never returned has nothing to extract from.

        crawl4ai leaves links={} and tables=[] on a hard failure, and a block
        page's own navigation is not the caller's data either.
        """
        page = _batch_result(
            [_result(success=False, links=RAW_LINKS, tables=[RAW_TABLE])],
            include_links=True,
            include_tables=True,
        ).pages[0]
        assert page.success is False
        assert page.links is None
        assert page.tables is None


class TestOutputDirPath:
    def test_links_stay_inline_when_content_goes_to_disk(self, tmp_path) -> None:
        """Only markdown has a file. Structured data the caller just asked for
        must not require reading files back to see it."""
        out = _persist_results(
            [_result(links=RAW_LINKS, tables=[RAW_TABLE])],
            str(tmp_path),
            include_links=True,
            include_tables=True,
        )
        page = out.pages[0]
        assert page.markdown is None
        assert page.file is not None
        assert page.links is not None
        assert page.links.internal[0].href == "https://en.wikipedia.org/wiki/Python"
        assert page.tables[0].headers == ["Country", "Population"]

    def test_output_dir_default_still_omits_them(self, tmp_path) -> None:
        out = _persist_results([_result(links=RAW_LINKS)], str(tmp_path))
        assert out.pages[0].links is None
