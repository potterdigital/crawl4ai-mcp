"""Tests for deep_crawl's allowed_domains / blocked_domains.

The `scope` enum cannot express "this docs site AND its separate api host",
which is what these two add. The subtle part is not the filter, it is the
ordering inside crawl4ai:

    BFSDeepCrawlStrategy.link_discovery applies include_external when choosing
    which links enter the candidate set at all, and can_process_url runs the
    filter chain afterwards on whatever survived.

So an allowlist naming an off-domain host is inert unless external links are
let through first. Nothing raises and nothing warns; the crawl simply stays
same-domain and the parameter does nothing. That is the regression these tests
exist for, and it is invisible to any test that only checks the filter chain
was built.
"""

import asyncio
from unittest.mock import MagicMock, patch

from crawl4ai.deep_crawling.filters import DomainFilter

from crawl4ai_mcp.server import deep_crawl


def _run_deep_crawl(**kwargs):
    """Invoke deep_crawl with the network stubbed, returning (strategy, result).

    The strategy object is the one actually handed to crawl4ai, so assertions
    below are on real configuration rather than on arguments in flight.
    """
    captured: dict = {}

    async def _fake_arun(url, config, **_):
        captured["strategy"] = config.deep_crawl_strategy

        async def _stream():
            page = MagicMock()
            page.url = url
            page.success = True
            page.status_code = 200
            page.metadata = {"depth": 0}
            page.markdown.fit_markdown = "content"
            page.markdown.raw_markdown = "content"
            page.links = {}
            page.tables = []
            page.crawl_stats = None
            page.redirected_url = None
            page.response_headers = None
            yield page

        return _stream()

    crawler = MagicMock()
    crawler.arun = _fake_arun

    ctx = MagicMock()
    ctx.request_context.lifespan_context = MagicMock()
    ctx.report_progress = MagicMock(return_value=asyncio.sleep(0))

    with patch("crawl4ai_mcp.server._require_crawler", return_value=crawler):
        out = asyncio.run(deep_crawl(ctx=ctx, **kwargs))
    return captured["strategy"], out


def _domain_filters(strategy) -> list[DomainFilter]:
    return [f for f in strategy.filter_chain.filters if isinstance(f, DomainFilter)]


class TestFilterIsBuilt:
    def test_no_domain_filter_by_default(self) -> None:
        strategy, _ = _run_deep_crawl(url="https://docs.example.com")
        assert _domain_filters(strategy) == []

    def test_allowed_domains_adds_the_filter(self) -> None:
        strategy, _ = _run_deep_crawl(
            url="https://docs.example.com",
            allowed_domains=["docs.example.com", "api.example.com"],
        )
        assert len(_domain_filters(strategy)) == 1

    def test_blocked_domains_adds_the_filter(self) -> None:
        strategy, _ = _run_deep_crawl(
            url="https://example.com", blocked_domains=["ads.example.com"]
        )
        assert len(_domain_filters(strategy)) == 1

    def test_domain_filter_composes_with_url_patterns(self) -> None:
        """Both filter kinds must survive; one must not replace the chain."""
        strategy, _ = _run_deep_crawl(
            url="https://example.com",
            include_pattern="/docs/*",
            allowed_domains=["example.com"],
        )
        assert len(strategy.filter_chain.filters) == 2


class TestScopeInteraction:
    """The ordering trap: include_external gates the pool before filters run."""

    def test_allowed_domains_lets_external_links_through(self) -> None:
        """Without this the allowlist is silently inert.

        scope defaults to same-domain, which sets include_external=False, and
        crawl4ai drops off-domain links before the filter chain ever sees them.
        api.example.com would never be crawled and nothing would say why.
        """
        strategy, _ = _run_deep_crawl(
            url="https://docs.example.com",
            allowed_domains=["docs.example.com", "api.example.com"],
        )
        assert strategy.include_external is True

    def test_allowlist_still_bounds_the_crawl(self) -> None:
        """Widening the pool must not mean crawling the open web.

        include_external=True alone would follow every outbound link. The
        allowlist is what keeps the crawl bounded, and more tightly than the
        scope it replaced.
        """
        strategy, _ = _run_deep_crawl(
            url="https://docs.example.com",
            allowed_domains=["docs.example.com", "api.example.com"],
        )
        domain_filter = _domain_filters(strategy)[0]
        assert domain_filter.apply("https://api.example.com/v1") is True
        assert domain_filter.apply("https://docs.example.com/guide") is True
        assert domain_filter.apply("https://unrelated.test/page") is False

    def test_blocked_domains_alone_does_not_widen_scope(self) -> None:
        """blocked_domains subtracts, so it must leave same-domain scope alone.

        Widening on a block list would turn "skip this subdomain" into "now
        also crawl the entire internet", which is the opposite of the ask.
        """
        strategy, _ = _run_deep_crawl(
            url="https://example.com", blocked_domains=["ads.example.com"]
        )
        assert strategy.include_external is False

    def test_explicit_any_scope_is_preserved(self) -> None:
        strategy, _ = _run_deep_crawl(
            url="https://example.com", scope="any", blocked_domains=["ads.example.com"]
        )
        assert strategy.include_external is True

    def test_subdomains_of_an_allowed_domain_are_included(self) -> None:
        """crawl4ai's rule everywhere else, so it must hold here too."""
        strategy, _ = _run_deep_crawl(
            url="https://example.com", allowed_domains=["example.com"]
        )
        domain_filter = _domain_filters(strategy)[0]
        assert domain_filter.apply("https://api.example.com/v1") is True

    def test_blocked_beats_allowed(self) -> None:
        """Carving one subdomain out of an allowed parent must work."""
        strategy, _ = _run_deep_crawl(
            url="https://example.com",
            allowed_domains=["example.com"],
            blocked_domains=["ads.example.com"],
        )
        domain_filter = _domain_filters(strategy)[0]
        assert domain_filter.apply("https://ads.example.com/x") is False
        assert domain_filter.apply("https://docs.example.com/x") is True


class TestStartHostNotCovered:
    """An allowlist excluding the start host does something surprising.

    Depth 0 bypasses the filter chain, so the start page is fetched; links back
    into its own site are then all rejected, while links to the listed hosts
    are followed. Measured live: starting at docs.astral.sh/uv/ with
    allowed_domains=["github.com"] crawled the start page plus three github
    pages. crawl4ai runs this without complaint, so the note is the only thing
    that tells the caller their own site was skipped.
    """

    def test_note_explains_the_skipped_start_site(self) -> None:
        _, out = _run_deep_crawl(
            url="https://docs.example.com/start",
            allowed_domains=["api.example.com"],
        )
        assert out.note is not None
        assert "allowed_domains does not cover the start URL's own host" in out.note
        assert "docs.example.com" in out.note

    def test_note_does_not_claim_a_single_page_result(self) -> None:
        """The first version of this note said "only that page was crawled".

        That was measurably false: links to the allowed hosts are still
        followed, so the crawl returns several pages. A note that misdescribes
        the result is worse than no note, because it sends the caller looking
        for a bug that is not there.
        """
        _, out = _run_deep_crawl(
            url="https://docs.example.com/start",
            allowed_domains=["api.example.com"],
        )
        assert "only that page was crawled" not in out.note

    def test_no_note_when_the_start_host_is_covered(self) -> None:
        _, out = _run_deep_crawl(
            url="https://docs.example.com/start",
            allowed_domains=["docs.example.com", "api.example.com"],
        )
        assert out.note is None

    def test_no_note_when_covered_by_a_parent_domain(self) -> None:
        """The check must use crawl4ai's subdomain rule, not string equality.

        Reimplementing the match here would fire a false warning on the most
        natural call there is: allowlisting the registrable domain.
        """
        _, out = _run_deep_crawl(
            url="https://docs.example.com/start", allowed_domains=["example.com"]
        )
        assert out.note is None

    def test_note_fires_when_the_start_host_is_blocked(self) -> None:
        _, out = _run_deep_crawl(
            url="https://docs.example.com/start",
            allowed_domains=["example.com"],
            blocked_domains=["docs.example.com"],
        )
        assert out.note is not None

    def test_no_note_without_an_allowlist(self) -> None:
        _, out = _run_deep_crawl(
            url="https://docs.example.com/start", blocked_domains=["ads.example.com"]
        )
        assert out.note is None
