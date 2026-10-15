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
