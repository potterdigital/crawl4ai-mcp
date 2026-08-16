"""Tests for the failure diagnostics attached to every crawl error.

crawl4ai fills `crawl_stats`, `redirected_url` and `response_headers` on its
own, and this server used to discard all three and report only status code and
error message. The difference matters most on a block: "Blocked by anti-bot
protection" alone cannot tell a caller whether the host refused once or refused
every retry across a proxy and an HTTP fallback.

Every fixture below is a shape recorded from a real crawl against a live host
(httpbin.org, en.wikipedia.org, a non-resolving domain) rather than one
invented to fit the code. The failure mode each test guards is named on it.
"""

from unittest.mock import MagicMock

from crawl4ai_mcp.server import (
    _batch_result,
    _failure_diagnostics,
    _format_crawl_error,
    _one_line,
)


def _result(
    url: str = "https://example.com",
    status_code: int | None = None,
    error_message: str = "boom",
    crawl_stats: dict | None = None,
    redirected_url: str | None = None,
    redirected_status_code: int | None = None,
    response_headers: dict | None = None,
):
    """A failed CrawlResult carrying only the fields the diagnostics read."""
    result = MagicMock()
    result.url = url
    result.success = False
    result.status_code = status_code
    result.error_message = error_message
    result.crawl_stats = crawl_stats
    result.redirected_url = redirected_url
    result.redirected_status_code = redirected_status_code
    result.response_headers = response_headers
    result.metadata = {}
    result.markdown = None
    return result


# Recorded verbatim from `https://httpbin.org/status/429` with max_retries=1.
BLOCKED_429_STATS = {
    "attempts": 2,
    "retries": 1,
    "proxies_used": [
        {
            "proxy": None,
            "status_code": 429,
            "blocked": True,
            "reason": "HTTP 429 Too Many Requests",
        },
        {
            "proxy": None,
            "status_code": 429,
            "blocked": True,
            "reason": "HTTP 429 Too Many Requests",
        },
    ],
    "fallback_fetch_used": False,
    "resolved_by": None,
}

# Recorded verbatim from a successful crawl of en.wikipedia.org.
CLEAN_STATS = {
    "attempts": 1,
    "retries": 0,
    "proxies_used": [
        {"proxy": None, "status_code": 200, "blocked": False, "reason": ""}
    ],
    "fallback_fetch_used": False,
    "resolved_by": "direct",
}


class TestQuietWhenNothingToSay:
    """The diagnostics must not fire on ordinary failures.

    Guards the regression where every error grows three lines of "Attempts: 1,
    0 retries" boilerplate, which buries the cases where the numbers are the
    whole point.
    """

    def test_no_extra_fields_produces_no_lines(self) -> None:
        assert _failure_diagnostics("https://example.com", _result()) == []

    def test_single_clean_attempt_produces_no_lines(self) -> None:
        """attempts=1, retries=0, nothing blocked: silence."""
        assert (
            _failure_diagnostics(
                "https://example.com", _result(crawl_stats=CLEAN_STATS)
            )
            == []
        )

    def test_crawl_stats_none_is_not_an_error(self) -> None:
        """The commonest failure of all has crawl_stats=None.

        A plain single-attempt connection failure (DNS, refused) re-raises
        inside crawl4ai's retry loop before any result is built, so nothing is
        attached. Verified against a non-resolving domain. Reading it as a dict
        anyway would crash the error formatter for the most frequent error.
        """
        result = _result(
            status_code=None,
            error_message="Failed on navigating ACS-GOTO: net::ERR_NAME_NOT_RESOLVED",
            crawl_stats=None,
        )
        out = _format_crawl_error("https://nope.invalid", result)
        assert "ERR_NAME_NOT_RESOLVED" in out
        assert "Attempts:" not in out

    def test_non_dict_crawl_stats_is_ignored(self) -> None:
        """Guards an upstream type change turning this into an AttributeError."""
        assert (
            _failure_diagnostics("https://example.com", _result(crawl_stats=["nope"]))
            == []
        )


class TestBlockedDiagnostics:
    def test_retries_and_blocks_are_reported(self) -> None:
        """The 429 case: two attempts, one retry, both refused."""
        lines = _failure_diagnostics(
            "https://httpbin.org/status/429", _result(crawl_stats=BLOCKED_429_STATS)
        )
        assert "Attempts: 2 (1 retried after a block), 2 blocked" in lines
        assert "Blocked by: HTTP 429 Too Many Requests" in lines

    def test_repeated_reason_is_reported_once(self) -> None:
        """Both attempts failed identically; saying so twice is noise."""
        lines = _failure_diagnostics(
            "https://httpbin.org/status/429", _result(crawl_stats=BLOCKED_429_STATS)
        )
        assert sum(1 for line in lines if line.startswith("Blocked by:")) == 1

    def test_distinct_reasons_are_both_kept(self) -> None:
        """Deduping must not collapse genuinely different failures."""
        stats = {
            "attempts": 2,
            "retries": 1,
            "proxies_used": [
                {"blocked": True, "reason": "HTTP 429 Too Many Requests"},
                {"blocked": True, "reason": "Structural: no <body> tag (0 bytes)"},
            ],
            "fallback_fetch_used": False,
        }
        lines = _failure_diagnostics("https://example.com", _result(crawl_stats=stats))
        assert sum(1 for line in lines if line.startswith("Blocked by:")) == 2

    def test_blocked_without_retries_still_reports(self) -> None:
        """max_retries defaults to 0, so a real block usually has retries=0.

        Gating the whole section on retries > 0 would silence the default
        configuration, which is the one almost every caller runs.
        """
        stats = {
            "attempts": 1,
            "retries": 0,
            "proxies_used": [
                {"blocked": True, "reason": "HTTP 403 with near-empty response"}
            ],
            "fallback_fetch_used": False,
        }
        lines = _failure_diagnostics("https://example.com", _result(crawl_stats=stats))
        assert any(line.startswith("Attempts: 1") for line in lines)
        assert "Blocked by: HTTP 403 with near-empty response" in lines

    def test_multiline_reason_is_flattened_and_capped(self) -> None:
        """A Playwright failure reason carries a full multi-line call log.

        Recorded from httpstat.us: the reason ran to a navigation trace with
        embedded newlines. Pasted in raw it swamps the surrounding report.
        """
        reason = (
            "Failed on navigating ACS-GOTO:\nPage.goto: net::ERR_EMPTY_RESPONSE\n"
            'Call log:\n  - navigating to "https://httpstat.us/429"\n' + "x" * 500
        )
        stats = {
            "attempts": 1,
            "retries": 0,
            "proxies_used": [{"blocked": True, "reason": reason}],
        }
        lines = _failure_diagnostics("https://example.com", _result(crawl_stats=stats))
        blocked_line = next(line for line in lines if line.startswith("Blocked by:"))
        assert "\n" not in blocked_line
        assert len(blocked_line) < 250

    def test_fallback_outcome_is_reported(self) -> None:
        stats = {
            "attempts": 1,
            "retries": 0,
            "proxies_used": [{"blocked": True, "reason": "HTTP 403"}],
            "fallback_fetch_used": True,
            "resolved_by": None,
        }
        lines = _failure_diagnostics("https://example.com", _result(crawl_stats=stats))
        assert any("fallback was tried and also failed" in line for line in lines)


class TestRedirectDiagnostics:
    def test_redirect_reported_when_it_differs(self) -> None:
        result = _result(
            redirected_url="https://example.com/login",
            redirected_status_code=200,
        )
        lines = _failure_diagnostics("https://example.com/private", result)
        assert "Redirected to: https://example.com/login (HTTP 200)" in lines

    def test_same_url_is_not_reported_as_a_redirect(self) -> None:
        """crawl4ai sets redirected_url even when nothing redirected.

        Verified on httpbin.org/status/429, where redirected_url equalled the
        requested URL. Reporting it unconditionally would claim a redirect on
        every single error.
        """
        result = _result(redirected_url="https://httpbin.org/status/429")
        assert _failure_diagnostics("https://httpbin.org/status/429", result) == []


class TestRetryAfter:
    def test_retry_after_is_surfaced(self) -> None:
        """The one header worth acting on when a host throttles."""
        result = _result(status_code=429, response_headers={"Retry-After": "120"})
        assert "Retry-After: 120" in _failure_diagnostics("https://example.com", result)

    def test_retry_after_is_matched_case_insensitively(self) -> None:
        """HTTP header names are case-insensitive and crawl4ai lowercases them."""
        result = _result(status_code=429, response_headers={"retry-after": "60"})
        assert "Retry-After: 60" in _failure_diagnostics("https://example.com", result)

    def test_other_headers_are_not_dumped(self) -> None:
        """Only Retry-After. A full header dump on every error is noise."""
        result = _result(
            status_code=503,
            response_headers={"server": "nginx", "cf-ray": "abc", "date": "now"},
        )
        assert _failure_diagnostics("https://example.com", result) == []


class TestFormatCrawlError:
    def test_original_fields_survive(self) -> None:
        """The diagnostics are additive; nothing that was reported before is lost."""
        result = _result(status_code=429, error_message="Blocked by anti-bot")
        out = _format_crawl_error("https://example.com", result)
        assert out.startswith("Crawl failed\nURL: https://example.com\n")
        assert "HTTP status: 429" in out
        assert "Error: Blocked by anti-bot" in out


class TestBatchPagesCarryDiagnostics:
    def test_failed_page_error_includes_stats(self) -> None:
        """A batch crawl's per-page error gets the same detail as a single crawl."""
        result = _result(
            url="https://httpbin.org/status/429",
            status_code=429,
            error_message="Blocked by anti-bot protection: HTTP 429 Too Many Requests",
            crawl_stats=BLOCKED_429_STATS,
        )
        page = _batch_result([result]).pages[0]
        assert page.success is False
        assert page.error is not None
        assert page.error.startswith("Blocked by anti-bot protection")
        assert "Attempts: 2 (1 retried after a block), 2 blocked" in page.error

    def test_ordinary_failure_error_is_unchanged(self) -> None:
        """No stats means the error text is exactly the message, with no suffix."""
        result = _result(error_message="Page.goto timed out")
        page = _batch_result([result]).pages[0]
        assert page.error == "Page.goto timed out"


class TestOneLine:
    def test_short_text_is_untouched(self) -> None:
        assert _one_line("HTTP 429 Too Many Requests") == "HTTP 429 Too Many Requests"

    def test_newlines_and_runs_collapse(self) -> None:
        assert _one_line("a\n\n  b\tc") == "a b c"

    def test_truncation_respects_the_limit(self) -> None:
        assert len(_one_line("x" * 500, limit=50)) == 50
