"""Guards for the defaults this server deliberately sets against crawl4ai's own.

Each test here pins a value where this server disagrees with crawl4ai on
purpose. Upstream's default is the one that wins if the explicit argument is
ever dropped, and the rest of the suite cannot tell the difference, because it
mocks the crawl itself. So every test asserts on the real crawl4ai object, not
a mock of it.
"""

import pytest

from crawl4ai_mcp import server as srv


def test_certificates_are_verified_by_default(monkeypatch):
    """TLS certificates are verified unless the operator opts out.

    Regression: crawl4ai's BrowserConfig defaults ignore_https_errors to True.
    If _build_browser_config stops passing the argument explicitly, every crawl
    silently accepts any certificate again, and every other test still passes.
    """
    monkeypatch.delenv(srv.IGNORE_HTTPS_ERRORS_ENV, raising=False)
    assert srv._build_browser_config().ignore_https_errors is False


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1", True),
        ("true", True),
        (" YES ", True),
        ("0", False),
        ("false", False),
        ("enabled", False),
    ],
)
def test_only_an_explicit_yes_disables_verification(monkeypatch, value, expected):
    """Only 1/true/yes turns verification off; anything else fails safe.

    Regression: a polarity copied from _auto_repair_enabled (on unless
    0/false/no) would make a typo or an unrecognized value like "enabled"
    disable certificate verification for the whole process.
    """
    monkeypatch.setenv(srv.IGNORE_HTTPS_ERRORS_ENV, value)
    assert srv._build_browser_config().ignore_https_errors is expected


# Verbatim crawl4ai 0.9.5 error_message values, recorded live on 2026-10-15 by
# scripts/live_check.py: the first from the tls scenario's crawl_url of
# https://self-signed.badssl.com/ with verification on, the second from a
# crawl of https://this-domain-does-not-exist-xyz123.com (a DNS failure).
RECORDED_CERT_ERROR = 'Unexpected error in _crawl_web at line 779 in _crawl_web (.venv/lib/python3.12/site-packages/crawl4ai/async_crawler_strategy.py):\nError: Failed on navigating ACS-GOTO:\nPage.goto: net::ERR_CERT_AUTHORITY_INVALID at https://self-signed.badssl.com/\nCall log:\n  - navigating to "https://self-signed.badssl.com/", waiting until "domcontentloaded"\n\n\nCode context:\n 774                                   tag="GOTO",\n 775                                   params={"url": url},\n 776                               )\n 777                               response = None\n 778                           else:\n 779 →                             raise RuntimeError(f"Failed on navigating ACS-GOTO:\\n{str(e)}")\n 780   \n 781                       # ──────────────────────────────────────────────────────────────\n 782                       # Walk the redirect chain.  Playwright returns only the last\n 783                       # hop, so we trace the `request.redirected_from` links until the\n 784                       # first response that differs from the final one and surface its'
RECORDED_DNS_ERROR = 'Unexpected error in _crawl_web at line 779 in _crawl_web (.venv/lib/python3.12/site-packages/crawl4ai/async_crawler_strategy.py):\nError: Failed on navigating ACS-GOTO:\nPage.goto: net::ERR_NAME_NOT_RESOLVED at https://this-domain-does-not-exist-xyz123.com/\nCall log:\n  - navigating to "https://this-domain-does-not-exist-xyz123.com/", waiting until "domcontentloaded"\n\n\nCode context:\n 774                                   tag="GOTO",\n 775                                   params={"url": url},\n 776                               )\n 777                               response = None\n 778                           else:\n 779 →                             raise RuntimeError(f"Failed on navigating ACS-GOTO:\\n{str(e)}")\n 780   \n 781                       # ──────────────────────────────────────────────────────────────\n 782                       # Walk the redirect chain.  Playwright returns only the last\n 783                       # hop, so we trace the `request.redirected_from` links until the\n 784                       # first response that differs from the final one and surface its'


def _failed_result(error_message: str):
    """A failed CrawlResult as crawl4ai builds it for a navigation error."""
    from unittest.mock import MagicMock

    result = MagicMock()
    result.error_message = error_message
    result.redirected_url = None
    result.response_headers = None
    result.crawl_stats = None
    return result


def test_a_certificate_failure_names_the_opt_out():
    """A rejected certificate says how to crawl the site anyway.

    Regression: without the hint, the first anyone hears of the 2.5.0 default
    is a Playwright stack trace ending in net::ERR_CERT_AUTHORITY_INVALID, and
    nothing says verification is now on or which variable turns it off. The
    fixture is the verbatim upstream text, so a change in crawl4ai's wording
    that the hint no longer recognises fails here.
    """
    lines = srv._failure_diagnostics(
        "https://self-signed.badssl.com/", _failed_result(RECORDED_CERT_ERROR)
    )
    assert any(srv.IGNORE_HTTPS_ERRORS_ENV in line for line in lines)


def test_a_non_certificate_failure_does_not_mention_the_opt_out():
    """Regression: a hint matched too loosely (any net:: error) would tell
    someone with a typo in a hostname to disable certificate verification."""
    lines = srv._failure_diagnostics(
        "https://this-domain-does-not-exist-xyz123.com", _failed_result(RECORDED_DNS_ERROR)
    )
    assert not any(srv.IGNORE_HTTPS_ERRORS_ENV in line for line in lines)


# A code block sitting between two prose paragraphs. Without preserve_tags the
# pruner scores the <pre> as low-density noise and drops it; measured on 0.9.5
# against both the BeautifulSoup filter and PruningContentFilterLXML.
_PROSE = (
    "This paragraph explains how to install the tool and what each step of the "
    "setup does for a new user. "
) * 3
_CODE_PAGE = (
    f"<html><body><article><p>{_PROSE}</p>"
    "<pre><code><span>uvx</span> <span>pycowsay</span> <span>hello</span></code></pre>"
    f"<p>{_PROSE}</p></article></body></html>"
)


def test_the_default_content_filter_keeps_code_blocks_silently():
    """The default filter keeps <pre>/<code>, and building it emits no warning.

    Regressions, any of which this catches:
    - switching to PruningContentFilterLXML, crawl4ai's suggested replacement,
      which has no preserve_tags and drops the block (and every <code> on
      Python's asyncio docs);
    - upstream turning PruningContentFilter into an alias of the lxml class,
      which it has announced;
    - losing the narrow warning filter, so crawl4ai's deprecation warning
      lands on stderr once per crawl.
    """
    import warnings

    from crawl4ai_mcp.profiles import ProfileManager, build_run_config

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        cf = build_run_config(ProfileManager(), None).markdown_generator.content_filter
    assert not [
        w
        for w in caught
        if issubclass(w.category, DeprecationWarning) and "PruningContentFilter" in str(w.message)
    ]
    assert "pycowsay" in "".join(cf.filter_content(_CODE_PAGE))


@pytest.mark.parametrize(("value", "expected"), [(None, "True"), ("0", "False")])
def test_cloud_notice_is_off_unless_the_user_turns_it_on(value, expected):
    """Importing the server silences crawl4ai's Cloud banner, but never
    overrides an operator who set the variable.

    Regressions: the variable name drifting from upstream's (the assertion goes
    through crawl4ai's own is_off(), so a misspelling fails), and setdefault
    replaced by plain assignment, which would override the operator's "0".
    Runs in a subprocess because the setting happens at import time.
    """
    import os
    import subprocess
    import sys

    env = dict(os.environ)
    env.pop("CRAWL4AI_NO_CLOUD_NOTICE", None)
    if value is not None:
        env["CRAWL4AI_NO_CLOUD_NOTICE"] = value
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import crawl4ai_mcp.server, crawl4ai.cloud_notice as n; print(n.is_off())",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().splitlines()[-1] == expected
