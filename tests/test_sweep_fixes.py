"""Regressions for the defects found by the v2.3.0 live conformance sweep.

Seven suites drove the real stdio server against real sites. Everything here
was green in the unit suite at the time, because the unit suite mocks
CrawlResult and never executes a tool body. Each test below names the failure
it guards and, where the number came from a measurement, states it.
"""

import asyncio
import inspect
import os
import stat
from unittest.mock import MagicMock, patch

import pytest

from crawl4ai_mcp.server import (
    DEFAULT_CACHE_MODE,
    CrawlBatchResult,
    _check_api_key,
    _maybe_gunzip,
    _persist_results,
    _resolve_cache_mode,
    crawl_many,
    crawl_sitemap,
    crawl_url,
    deep_crawl,
    extract_patterns,
)

CRAWL_TOOLS = (crawl_url, crawl_many, crawl_sitemap, deep_crawl)


def _result(url: str = "https://example.com", success: bool = True):
    r = MagicMock()
    r.url = url
    r.success = success
    r.status_code = 200 if success else None
    r.error_message = "" if success else "boom"
    r.metadata = {}
    r.links = {}
    r.tables = []
    r.crawl_stats = None
    r.redirected_url = None
    r.response_headers = None
    if success:
        r.markdown.fit_markdown = "page content"
        r.markdown.raw_markdown = "page content"
    else:
        r.markdown = None
    return r


class TestGzipByContentNotByUrl:
    """`crawl_sitemap` crashed outright on a valid sitemap.

    Decompression keyed off `sitemap_url.endswith(".gz")`, a claim about the
    REQUEST. httpx transparently decodes `Content-Encoding: gzip`, so a server
    setting that header on a .gz path hands us plain bytes at a .gz URL, and
    `gzip.decompress` raised `BadGzipFile: Not a gzipped file (b'<?')`. Nothing
    caught it, so the tool call died. Reproduced live before the fix.
    """

    def test_plain_bytes_pass_through(self) -> None:
        xml = b'<?xml version="1.0"?><urlset/>'
        assert _maybe_gunzip(xml) == xml

    def test_real_gzip_is_decompressed(self) -> None:
        import gzip

        xml = b'<?xml version="1.0"?><urlset/>'
        assert _maybe_gunzip(gzip.compress(xml)) == xml

    def test_truncated_gzip_returns_bytes_instead_of_raising(self) -> None:
        """A half-written .gz must reach the XML parser, not kill the tool.

        The XML parser's error names the sitemap, which is what the caller can
        act on; a gzip error names a compression format they never mentioned.
        """
        import gzip

        broken = gzip.compress(b"<urlset/>")[:12]
        assert _maybe_gunzip(broken) == broken

    def test_decision_ignores_the_url_entirely(self) -> None:
        """The signature must not accept a URL, or the old bug can return."""
        params = inspect.signature(_maybe_gunzip).parameters
        assert list(params) == ["content"]


class TestOutputDirFailureKeepsTheCrawl:
    """An unwritable output_dir threw away a completed crawl.

    `os.makedirs` / `open` raised OSError straight out of the tool, so a
    successful crawl of N pages answered with "Error executing tool crawl_many:
    [Errno 13] Permission denied". The network work was done and every page was
    in hand. It also broke this repo's rule that tools never raise for an
    expected failure, and an unwritable directory is expected.
    """

    def test_unwritable_dir_returns_content_inline(self, tmp_path) -> None:
        ro = tmp_path / "readonly"
        ro.mkdir()
        os.chmod(ro, stat.S_IRUSR | stat.S_IXUSR)
        try:
            out = _persist_results([_result()], str(ro / "sub"))
            assert isinstance(out, CrawlBatchResult)
            assert out.crawled == 1
            assert out.pages[0].markdown == "page content"
            assert out.note is not None
            assert "Could not write to output_dir" in out.note
        finally:
            os.chmod(ro, stat.S_IRWXU)

    def test_existing_note_is_preserved(self, tmp_path) -> None:
        """A sitemap truncation note must not be lost to a disk failure."""
        ro = tmp_path / "ro2"
        ro.mkdir()
        os.chmod(ro, stat.S_IRUSR | stat.S_IXUSR)
        try:
            out = _persist_results(
                [_result()], str(ro / "sub"), note="Sitemap truncated at 500."
            )
            assert "Sitemap truncated at 500." in out.note
            assert "Could not write" in out.note
        finally:
            os.chmod(ro, stat.S_IRWXU)

    def test_writable_dir_still_writes_files(self, tmp_path) -> None:
        """Control: the happy path must be untouched by the error handling."""
        out = _persist_results([_result()], str(tmp_path / "ok"))
        assert out.pages[0].file is not None
        assert out.pages[0].markdown is None
        assert (tmp_path / "ok" / "manifest.json").exists()


class TestCacheDefault:
    """crawl4ai's cache does not preserve the filtered markdown.

    Measured on one docs page with a BM25 query: first crawl 1,848 chars of the
    relevant sections, identical second crawl served from cache 21,767 chars of
    the whole page, with status_code=None. Under the old default of "enabled"
    that meant crawling any page twice silently returned different, 12x larger,
    wrongly-labelled content.
    """

    def test_default_is_bypass(self) -> None:
        assert DEFAULT_CACHE_MODE == "bypass"

    def test_none_resolves_to_the_default(self) -> None:
        from crawl4ai import CacheMode

        mode, err = _resolve_cache_mode(None)
        assert mode == CacheMode.BYPASS
        assert err is None

    def test_every_crawl_tool_defers_to_the_default(self) -> None:
        """A hardcoded "enabled" default on any tool reintroduces the bug."""
        for tool in CRAWL_TOOLS:
            assert inspect.signature(tool).parameters["cache_mode"].default is None, (
                tool.__name__
            )

    def test_explicit_values_are_honoured(self) -> None:
        from crawl4ai import CacheMode

        assert _resolve_cache_mode("enabled")[0] == CacheMode.ENABLED
        assert _resolve_cache_mode("  ENABLED ")[0] == CacheMode.ENABLED

    def test_unrecognised_value_is_refused_not_defaulted(self) -> None:
        """Silently downgrading a typo left the caller running a setting they
        did not choose, with the only signal on stderr, which no MCP client
        reads."""
        _, err = _resolve_cache_mode("bypas")
        assert err is not None
        assert "bypas" in err
        assert "bypass" in err


class TestProfileValuesCanWin:
    """Python defaults were being applied as if they were explicit overrides.

    `cache_mode` and `page_timeout` were written into the run config
    unconditionally, and `word_count_threshold` was guarded on `!= 10`. The
    server could not tell "caller said 60" from "caller said nothing", so a
    profile's own values could never take effect. Proven live: profile "fast"
    declares page_timeout 15s and a crawl still failed with "Timeout 60000ms".
    """

    @pytest.mark.parametrize(
        "param", ["page_timeout", "word_count_threshold", "cache_mode"]
    )
    def test_crawl_tools_default_to_none(self, param: str) -> None:
        for tool in CRAWL_TOOLS:
            assert inspect.signature(tool).parameters[param].default is None, (
                f"{tool.__name__}.{param}"
            )

    @pytest.mark.parametrize("param", ["page_timeout", "word_count_threshold"])
    def test_omitted_params_never_reach_the_config(self, param: str) -> None:
        """The guard, not just the default. A default of None that is written
        anyway would pass the test above and still clobber the profile."""
        for tool in CRAWL_TOOLS:
            src = inspect.getsource(tool)
            assert f"if {param} is not None:" in src, f"{tool.__name__}.{param}"


class TestDeepCrawlMaxPages:
    """BFS returned one page fewer than asked, and zero at max_pages=1.

    crawl4ai's `_arun_stream` counts a successful page then `break`s BEFORE the
    `yield`, and deep_crawl forces stream=True so it can report progress.
    Measured against crawl4ai's own strategy: stream=False gives 1/3/5,
    stream=True gives 0/2/4. Compensated by requesting one extra and truncating,
    which lands on exactly max_pages whether or not upstream is ever fixed.
    """

    def test_bfs_requests_one_extra(self) -> None:
        src = inspect.getsource(deep_crawl)
        assert "max_pages=max_pages + 1" in src

    def test_best_first_does_not(self) -> None:
        """best-first has no off-by-one; padding it would over-deliver."""
        src = inspect.getsource(deep_crawl)
        best_first = src.split('if strategy == "best-first":')[1].split("else:")[0]
        assert "max_pages=max_pages," in best_first

    def test_results_are_truncated_to_the_cap(self) -> None:
        """The half that keeps the +1 safe if upstream fixes the bug."""
        src = inspect.getsource(deep_crawl)
        assert "if len(results) > max_pages:" in src
        assert "results = results[:max_pages]" in src


class TestUnknownEnumsAreRefused:
    def test_scope_and_strategy_refuse(self) -> None:
        src = inspect.getsource(deep_crawl)
        assert '_bad_choice("scope"' in src
        assert '_bad_choice("strategy"' in src


class TestCustomPatternsDoNotCrash:
    """An invalid regex took the whole tool call down.

    `re.compile` raised from inside crawl4ai, so the caller got
    "Error executing tool extract_patterns" with no structuredContent and no
    indication of WHICH pattern was bad.
    """

    def _run(self, custom):
        ctx = MagicMock()
        ctx.request_context.lifespan_context = MagicMock()
        return asyncio.run(
            extract_patterns(url="https://example.com", custom_patterns=custom, ctx=ctx)
        )

    def test_invalid_regex_is_reported_not_raised(self) -> None:
        out = self._run({"broken": "([unclosed"})
        assert out.count == 0
        assert "not a valid regular expression" in out.error

    def test_the_bad_pattern_is_named(self) -> None:
        """With five patterns supplied, "invalid regex" alone is not actionable."""
        out = self._run({"good": r"\d+", "bad": "(?P<>x)"})
        assert "'bad'" in out.error

    def test_non_string_pattern_is_reported(self) -> None:
        out = self._run({"oops": 42})
        assert out.count == 0
        assert "must be a regex string" in out.error


class TestUnknownProviderRefused:
    """litellm routes an unrecognised provider to OpenAI rather than rejecting
    it, so a typo billed a vendor the caller never named.

    The pair of tests below matters more than either alone. Validating against
    PROVIDER_ENV_VARS catches the typo but also rejects Mistral, Azure, Bedrock
    and the ~120 other providers litellm supports whose key name this server
    has no reason to know. The check therefore asks litellm, not us.
    """

    def test_typo_is_refused(self) -> None:
        err = _check_api_key("gemin/gemini-2.5-flash")
        assert err is not None
        assert "Unknown provider" in err
        # Must say WHY refusing beats attempting, or it reads as pedantry.
        assert "OpenAI" in err

    @pytest.mark.parametrize(
        "provider", ["mistral/mistral-large", "azure/gpt-4o", "bedrock/claude-3"]
    )
    def test_real_litellm_providers_are_not_blocked(self, provider: str) -> None:
        """Guards the over-correction: a stricter check that rejected these
        would still pass the typo test above."""
        assert _check_api_key(provider) is None

    def test_provider_list_comes_from_litellm(self) -> None:
        """A hand-maintained copy would drift the moment litellm adds one."""
        from crawl4ai_mcp.server import _known_provider_prefixes

        known = _known_provider_prefixes()
        assert len(known) > 50, "expected litellm's full provider list"
        assert {"openai", "anthropic", "mistral"} <= known


class TestSessionRegistrationOnFailure:
    """A failed crawl carrying session_id left an unreachable session.

    crawl4ai registers the session during page setup, before navigation, so the
    session existed with its injected cookies while list_sessions could not show
    it and destroy_session could not reach it. destroy_session is the only thing
    that clears a session's cookies, so this removed the one available remedy.
    """

    def test_registration_precedes_the_failure_return(self) -> None:
        src = inspect.getsource(crawl_url)
        register = src.index("app.sessions[session_id] = time.time()")
        fail_return = src.index("return _format_crawl_error(url, result)")
        assert register < fail_return, (
            "session registration must happen before the failure return, or a "
            "failed crawl leaves a session nothing can destroy"
        )


class TestInjectedCookieCleanupIsScoped:
    """Cleanup cleared a cookie NAME across every context.

    "session", "sid", "auth_token" and "session_token" are exactly the names
    everyone reuses, so one call's cleanup silently deleted a live session's
    identically-named cookie and de-authenticated a workflow midway through.
    """

    def test_domain_and_path_are_passed_to_clear(self) -> None:
        from crawl4ai_mcp.server import _clear_injected_cookies

        cleared: list[dict] = []

        class Ctx:
            async def clear_cookies(self, **kw):
                cleared.append(kw)

        crawler = MagicMock()
        crawler.crawler_strategy.browser_manager.default_context = Ctx()
        crawler.crawler_strategy.browser_manager.contexts_by_config = {}

        asyncio.run(
            _clear_injected_cookies(
                crawler,
                [{"name": "session", "value": "v", "domain": "a.test", "path": "/x"}],
            )
        )
        assert cleared == [{"name": "session", "domain": "a.test", "path": "/x"}]


class TestCreateSessionCookiesWithoutUrl:
    """create_session reported success while dropping every cookie.

    The no-url branch crawled "about:blank" to fire the injection hook.
    crawl4ai rejects that URL outright, so the hook never ran, and the failed
    result was discarded without checking success.
    """

    def test_warns_that_cookies_were_not_applied(self) -> None:
        from crawl4ai_mcp.server import create_session

        app = MagicMock()
        app.sessions = {}
        ctx = MagicMock()
        ctx.request_context.lifespan_context = app

        with patch("crawl4ai_mcp.server._crawl_with_overrides") as crawl:
            out = asyncio.run(
                create_session(
                    session_id="s1",
                    cookies=[{"name": "a", "value": "b", "domain": "x.test"}],
                    ctx=ctx,
                )
            )
            crawl.assert_not_called()
        assert "NOT applied" in out
        assert "url" in out

    def test_no_warning_when_no_cookies_passed(self) -> None:
        from crawl4ai_mcp.server import create_session

        app = MagicMock()
        app.sessions = {}
        ctx = MagicMock()
        ctx.request_context.lifespan_context = app
        out = asyncio.run(create_session(session_id="s2", ctx=ctx))
        assert "NOT applied" not in out


class TestDocumentedContracts:
    """Docs the sweep proved wrong or missing. Each was a real miss."""

    def test_query_is_documented_on_every_crawl_tool(self) -> None:
        for tool in CRAWL_TOOLS:
            assert "query:" in (tool.__doc__ or ""), tool.__name__

    def test_user_agent_stickiness_is_documented_everywhere(self) -> None:
        """Documented on crawl_url only; the other three promised nothing.

        Whitespace is normalised before matching because these docstrings are
        wrapped: asserting on a raw substring silently depends on where the
        line happens to break, which is how this test first failed against
        text that was in fact present.
        """
        for tool in CRAWL_TOOLS:
            doc = " ".join((tool.__doc__ or "").split())
            assert "wins for that context's lifetime" in doc, tool.__name__

    def test_xpath_absolute_selector_claim_is_corrected(self) -> None:
        """The docstring claimed a leading // searches the whole document.

        It does not: crawl4ai evaluates field selectors context-sensitively and
        re-roots //foo to .//foo. Proven with //title, which returned empty
        rather than repeating the page title.
        """
        from crawl4ai_mcp.server import extract_css

        doc = extract_css.__doc__ or ""
        assert "searches the whole document again" not in doc
        assert "re-roots" in doc

    def test_cookie_sharing_is_documented(self) -> None:
        """Documentation is the entire mitigation for this one, so its absence
        is the defect."""
        doc = crawl_url.__doc__ or ""
        assert "SECURITY" in doc
        assert "session" in doc and "shared" in doc.lower()

    def test_session_is_not_advertised_as_isolation(self) -> None:
        from crawl4ai_mcp.server import create_session

        assert "NOT a security boundary" in (create_session.__doc__ or "")
