"""Tests that per-call credentials do not escape the call that supplied them.

Two real leaks, both demonstrated against a live local server before the fix:

1. Cookies outlived their call. `context.add_cookies()` writes into a browser
   context that crawl4ai caches and reuses, and closing the page does not close
   the context. So a crawl that injected a session cookie authenticated every
   later crawl of that domain. Observed: call one fetched /private with
   session_token=SUPER-SECRET; call two fetched /public passing NO cookies at
   all, and the server still received session_token=SUPER-SECRET.

2. Headers crossed between concurrent calls. crawl4ai keeps ONE hook slot per
   hook type on the strategy, and this server shares a single crawler across
   every tool call, so writing a call's headers there published them to every
   other in-flight call. Observed: a request to host A carried host B's bearer
   token, and whichever call finished first cleared the slot out from under the
   other.

Both traced to the same root cause: per-call data written into state shared by
a singleton. The fix keeps the data in a ContextVar (scoped to the task) and
clears injected cookies afterwards.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from crawl4ai_mcp import server as srv


class FakeContext:
    """Stands in for a Playwright BrowserContext, recording cookie operations."""

    def __init__(self) -> None:
        self.cookies: list[dict] = []
        self.cleared: list[str] = []

    async def add_cookies(self, cookies):
        self.cookies.extend(cookies)

    async def clear_cookies(self, *, name=None, domain=None, path=None):
        self.cleared.append(name)
        self.cookies = [c for c in self.cookies if c.get("name") != name]


def _crawler_with(context: FakeContext) -> MagicMock:
    crawler = MagicMock()
    crawler.crawler_strategy.browser_manager.default_context = context
    crawler.crawler_strategy.browser_manager.contexts_by_config = {}
    return crawler


class TestCookiesDoNotOutliveTheirCall:
    def test_injected_cookies_are_cleared_afterwards(self) -> None:
        """Without this, the next crawl of that domain is silently authenticated."""
        ctx = FakeContext()
        crawler = _crawler_with(ctx)
        crawler.arun = AsyncMock(return_value="ok")
        cookies = [{"name": "session_token", "value": "SECRET", "domain": "x.test"}]

        asyncio.run(
            srv._crawl_with_overrides(
                crawler, "https://x.test/", MagicMock(session_id=None), cookies=cookies
            )
        )

        assert "session_token" in ctx.cleared

    def test_cookies_are_cleared_even_when_the_crawl_raises(self) -> None:
        """A failed crawl must not leave credentials behind either."""
        ctx = FakeContext()
        crawler = _crawler_with(ctx)
        crawler.arun = AsyncMock(side_effect=RuntimeError("boom"))
        cookies = [{"name": "tok", "value": "S", "domain": "x.test"}]

        with pytest.raises(RuntimeError):
            asyncio.run(
                srv._crawl_with_overrides(
                    crawler,
                    "https://x.test/",
                    MagicMock(session_id=None),
                    cookies=cookies,
                )
            )

        assert "tok" in ctx.cleared

    def test_session_cookies_are_kept(self) -> None:
        """Persisting cookies across calls is the entire point of a session;
        clearing them here would break multi-step authenticated flows."""
        ctx = FakeContext()
        crawler = _crawler_with(ctx)
        crawler.arun = AsyncMock(return_value="ok")

        asyncio.run(
            srv._crawl_with_overrides(
                crawler,
                "https://x.test/",
                MagicMock(session_id="authed"),
                cookies=[{"name": "tok", "value": "S", "domain": "x.test"}],
            )
        )

        assert ctx.cleared == []

    def test_contexts_beyond_the_default_are_also_cleared(self) -> None:
        """crawl4ai caches a context per config signature; the crawl may have
        used one of those rather than the default."""
        default, cached = FakeContext(), FakeContext()
        crawler = MagicMock()
        crawler.crawler_strategy.browser_manager.default_context = default
        crawler.crawler_strategy.browser_manager.contexts_by_config = {"sig": cached}
        crawler.arun = AsyncMock(return_value="ok")

        asyncio.run(
            srv._crawl_with_overrides(
                crawler,
                "https://x.test/",
                MagicMock(session_id=None),
                cookies=[{"name": "tok", "value": "S", "domain": "x.test"}],
            )
        )

        assert "tok" in default.cleared
        assert "tok" in cached.cleared


class TestOverridesAreScopedToTheirTask:
    def test_two_concurrent_calls_do_not_see_each_others_headers(self) -> None:
        """The bug this replaces sent host A's request with host B's token,
        because both calls wrote into one slot on the shared strategy."""
        seen: dict[str, dict | None] = {}

        async def scenario():
            async def fake_arun(url, config):
                # Read what the hook would read, at the moment this task runs.
                await asyncio.sleep(0.05 if "A" in url else 0.0)
                seen[url] = srv._call_overrides.get().get("headers")
                return "ok"

            crawler = _crawler_with(FakeContext())
            crawler.arun = fake_arun

            await asyncio.gather(
                srv._crawl_with_overrides(
                    crawler,
                    "https://A.test/",
                    MagicMock(session_id=None),
                    headers={"X-Auth": "TOKEN-A"},
                ),
                srv._crawl_with_overrides(
                    crawler,
                    "https://B.test/",
                    MagicMock(session_id=None),
                    headers={"X-Auth": "TOKEN-B"},
                ),
            )

        asyncio.run(scenario())

        assert seen["https://A.test/"] == {"X-Auth": "TOKEN-A"}
        assert seen["https://B.test/"] == {"X-Auth": "TOKEN-B"}

    def test_a_call_with_no_headers_inherits_nothing(self) -> None:
        """A crawl passing no headers must not pick up the previous call's."""
        ctx = FakeContext()
        crawler = _crawler_with(ctx)
        seen = {}

        async def scenario():
            async def fake_arun(url, config):
                seen[url] = srv._call_overrides.get().get("headers")
                return "ok"

            crawler.arun = fake_arun
            await srv._crawl_with_overrides(
                crawler,
                "https://first/",
                MagicMock(session_id=None),
                headers={"X-Auth": "SECRET"},
            )
            await srv._crawl_with_overrides(
                crawler, "https://second/", MagicMock(session_id=None)
            )

        asyncio.run(scenario())

        assert seen["https://first/"] == {"X-Auth": "SECRET"}
        assert seen["https://second/"] is None

    def test_hooks_are_installed_once_not_per_call(self) -> None:
        """Setting hooks per call is what made them shared state. They must be
        installed at startup and left alone."""
        crawler = MagicMock()
        srv._install_override_hooks(crawler)

        installed = {
            c.args[0] for c in crawler.crawler_strategy.set_hook.call_args_list
        }
        assert installed == {"before_goto", "on_page_context_created"}

    @pytest.mark.asyncio
    async def test_hooks_are_a_no_op_when_nothing_was_supplied(self) -> None:
        """They run on every crawl, so the empty path must be harmless."""
        page, context = AsyncMock(), AsyncMock()
        token = srv._call_overrides.set({})
        try:
            await srv._override_before_goto(page, context, "u", None)
            await srv._override_on_context(page, context)
        finally:
            srv._call_overrides.reset(token)

        page.set_extra_http_headers.assert_not_called()
        context.add_cookies.assert_not_called()
