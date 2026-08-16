"""Tests for browser-unavailable recovery.

The failure these guard: Playwright gets upgraded (usually by `uv sync`), the
matching Chromium build was never downloaded, and the server dies at startup.
The MCP client then shows only "failed to connect" and the remediation line is
buried in a connect log, so the cause is invisible.

Each test below pins one property of the fix:
- startup never exits on a missing browser (the regression that hid the cause)
- tool errors name the fix instead of raising AttributeError on a None crawler
- ping reports the degraded state rather than claiming health or blowing up
- a repair actually flips state to ready, and concurrent repairs install once
"""

import asyncio

import pytest

from crawl4ai_mcp import server as srv
from crawl4ai_mcp.server import AppContext, BrowserState, _require_crawler


def _ctx(
    status: str = "failed", detail: str = "boom", crawler: object = None
) -> AppContext:
    """AppContext in a chosen browser state, with no real crawler attached."""
    return AppContext(
        crawler=crawler,  # type: ignore[arg-type]
        profile_manager=object(),  # type: ignore[arg-type]
        sessions={},
        browser=BrowserState(status=status, detail=detail, started_at=0.0),
    )


class TestPreflightNeverExits:
    def test_missing_browser_does_not_exit(self, monkeypatch, capsys) -> None:
        """Preflight warns and returns. A SystemExit here means the silent-connect-failure bug is back."""
        monkeypatch.setattr(srv, "_chromium_status", lambda: (False, "no chromium"))

        srv._preflight_playwright()  # must not raise SystemExit

        err = capsys.readouterr().err
        assert "WARNING" in err
        assert "crawl4ai-setup" in err

    def test_healthy_browser_is_silent(self, monkeypatch, capsys) -> None:
        """No warning noise when the browser is fine."""
        monkeypatch.setattr(srv, "_chromium_status", lambda: (True, "/path/to/chrome"))

        srv._preflight_playwright()

        assert capsys.readouterr().err == ""


class TestRequireCrawler:
    def test_returns_live_crawler(self) -> None:
        sentinel = object()
        assert _require_crawler(_ctx(status="ready", crawler=sentinel)) is sentinel

    def test_failed_state_names_the_fix(self) -> None:
        """The error an agent receives must contain an actionable next step, not just a symptom."""
        with pytest.raises(RuntimeError) as exc:
            _require_crawler(_ctx(status="failed", detail="Executable doesn't exist"))

        msg = str(exc.value)
        assert "repair_browser" in msg
        assert "crawl4ai-setup" in msg
        assert "Executable doesn't exist" in msg

    def test_repairing_state_says_to_retry(self) -> None:
        """Mid-install is a distinct, temporary state — callers should retry, not go fix anything."""
        with pytest.raises(RuntimeError) as exc:
            _require_crawler(_ctx(status="repairing", detail=""))

        msg = str(exc.value)
        assert "installing" in msg.lower()
        assert "retry" in msg.lower()


class TestAutoRepairToggle:
    def test_enabled_by_default(self, monkeypatch) -> None:
        monkeypatch.delenv(srv.AUTO_REPAIR_ENV, raising=False)
        assert srv._auto_repair_enabled() is True

    @pytest.mark.parametrize("value", ["0", "false", "FALSE", "no"])
    def test_disabled_by_falsey_values(self, monkeypatch, value: str) -> None:
        monkeypatch.setenv(srv.AUTO_REPAIR_ENV, value)
        assert srv._auto_repair_enabled() is False


class TestRepairBrowser:
    async def test_successful_repair_makes_crawler_available(self, monkeypatch) -> None:
        """After a repair the context must hold a live crawler and report ready."""
        app = _ctx()
        sentinel = object()
        monkeypatch.setattr(srv, "_install_browser", lambda: (True, "/path/to/chrome"))

        async def fake_start():
            return sentinel, ""

        monkeypatch.setattr(srv, "_start_crawler", fake_start)

        ok, _ = await srv._repair_browser(app)

        assert ok is True
        assert app.crawler is sentinel
        assert app.browser.status == "ready"
        assert _require_crawler(app) is sentinel

    async def test_failed_install_records_reason_and_stays_degraded(
        self, monkeypatch
    ) -> None:
        """A failed install must leave a reportable reason, not a half state that looks ready."""
        app = _ctx()
        monkeypatch.setattr(
            srv, "_install_browser", lambda: (False, "network unreachable")
        )

        ok, detail = await srv._repair_browser(app)

        assert ok is False
        assert "network unreachable" in detail
        assert app.crawler is None
        assert app.browser.status == "failed"

    async def test_concurrent_repairs_install_once(self, monkeypatch) -> None:
        """Two callers must not start two competing downloads into the same cache."""
        app = _ctx()
        calls = {"n": 0}
        sentinel = object()

        def slow_install():
            calls["n"] += 1
            return True, "/path/to/chrome"

        async def fake_start():
            await asyncio.sleep(0.01)
            return sentinel, ""

        monkeypatch.setattr(srv, "_install_browser", slow_install)
        monkeypatch.setattr(srv, "_start_crawler", fake_start)

        await asyncio.gather(srv._repair_browser(app), srv._repair_browser(app))

        assert calls["n"] == 1
        assert app.crawler is sentinel
