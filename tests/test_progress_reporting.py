"""Tests for progress reporting on long-running tools.

The failure these guard, reproduced against a real Claude Code client before
the fix: a tool call that sends neither a response nor a progress
notification for the client's idle window is aborted outright. For stdio
that window is 30 minutes by default, and the batch crawls used to put
nothing on the wire from the moment they were called until every page was
done. The observed error was:

    MCP server "c4a" tool "crawl_many" sent no response or progress for
    30s; aborting.

That makes it a bug no ordinary test can see, because reproducing it takes
either a 30-minute crawl or a shrunken idle window on a live client. So the
tests below pin the mechanism instead, each on a distinct failure mode:

- the heartbeat stops firing, or fires only after the work is already done
- the heartbeat fires on short calls too, flooding the client
- the wrapper swallows the operation's result or its exception
- a failed notification takes down the crawl it was only reporting on
- progress values stop increasing, which the MCP spec forbids
- someone "simplifies" a tool back to a bare await, silently restoring the
  original bug
"""

import asyncio
import inspect

import pytest

from crawl4ai_mcp.server import (
    _await_with_heartbeat,
    _collect_with_progress,
    _emit_progress,
)


class RecordingCtx:
    """Stands in for the MCP Context, capturing progress notifications."""

    def __init__(self, fail: bool = False) -> None:
        self.calls: list[dict] = []
        self.fail = fail

    async def report_progress(
        self, progress: float, total: float | None = None, message: str | None = None
    ) -> None:
        if self.fail:
            raise RuntimeError("transport closed")
        self.calls.append({"progress": progress, "total": total, "message": message})


async def _sleep_then(value, seconds: float):
    await asyncio.sleep(seconds)
    return value


async def _agen(items, gap: float = 0.0):
    for item in items:
        if gap:
            await asyncio.sleep(gap)
        yield item


class _Page:
    def __init__(self, url: str) -> None:
        self.url = url


class TestHeartbeat:
    @pytest.mark.asyncio
    async def test_emits_while_a_slow_operation_runs(self) -> None:
        """Without this the call is silent end to end, which is the abort."""
        ctx = RecordingCtx()

        result = await _await_with_heartbeat(
            _sleep_then("done", 0.35), ctx, "Crawling", interval=0.1
        )

        assert result == "done"
        assert len(ctx.calls) >= 2, f"expected repeated heartbeats, got {ctx.calls}"

    @pytest.mark.asyncio
    async def test_stays_quiet_for_a_fast_operation(self) -> None:
        """A notification per short call would flood the client for no reason."""
        ctx = RecordingCtx()

        await _await_with_heartbeat(
            _sleep_then("done", 0.01), ctx, "Crawling", interval=5.0
        )

        assert ctx.calls == []

    @pytest.mark.asyncio
    async def test_progress_value_strictly_increases(self) -> None:
        """The MCP spec requires progress to increase on every notification."""
        ctx = RecordingCtx()

        await _await_with_heartbeat(
            _sleep_then(None, 0.35), ctx, "Crawling", interval=0.1
        )

        values = [c["progress"] for c in ctx.calls]
        assert values == sorted(set(values)), f"not strictly increasing: {values}"

    @pytest.mark.asyncio
    async def test_returns_the_operations_result_unchanged(self) -> None:
        """The wrapper is transparent; a crawl's results must survive it."""
        payload = [{"url": "https://example.com"}]

        got = await _await_with_heartbeat(
            _sleep_then(payload, 0.15), RecordingCtx(), "Crawling", interval=0.05
        )

        assert got is payload

    @pytest.mark.asyncio
    async def test_propagates_the_operations_exception(self) -> None:
        """A crawl failure must not be hidden by the progress wrapper."""

        async def boom():
            await asyncio.sleep(0.15)
            raise ValueError("crawl exploded")

        ctx = RecordingCtx()
        with pytest.raises(ValueError, match="crawl exploded"):
            await _await_with_heartbeat(boom(), ctx, "Crawling", interval=0.05)

    @pytest.mark.asyncio
    async def test_a_broken_notification_does_not_kill_the_crawl(self) -> None:
        """Progress is telemetry. If it cannot be delivered, the work still counts."""
        ctx = RecordingCtx(fail=True)

        result = await _await_with_heartbeat(
            _sleep_then("done", 0.25), ctx, "Crawling", interval=0.05
        )

        assert result == "done"


class TestEmitProgress:
    @pytest.mark.asyncio
    async def test_swallows_notification_failures(self) -> None:
        """_emit_progress is the guard that makes the above possible."""
        await _emit_progress(RecordingCtx(fail=True), 1, 10, "x")  # must not raise

    @pytest.mark.asyncio
    async def test_forwards_the_values_it_is_given(self) -> None:
        ctx = RecordingCtx()

        await _emit_progress(ctx, 3, 9, "halfway")

        assert ctx.calls == [{"progress": 3, "total": 9, "message": "halfway"}]


class TestCollectWithProgress:
    @pytest.mark.asyncio
    async def test_reports_once_per_page_and_returns_every_page(self) -> None:
        """deep_crawl streams; each page must both reset the idle timer and survive."""
        pages = [_Page(f"https://example.com/{i}") for i in range(4)]
        ctx = RecordingCtx()

        got = await _collect_with_progress(_agen(pages), ctx, total=4, label="Deep")

        assert got == pages
        assert len(ctx.calls) == 4
        assert [c["progress"] for c in ctx.calls] == [1, 2, 3, 4]
        assert all(c["total"] == 4 for c in ctx.calls)
        assert "https://example.com/2" in ctx.calls[2]["message"]

    @pytest.mark.asyncio
    async def test_handles_an_unknown_total(self) -> None:
        """total=None must still produce increasing progress, not a crash."""
        ctx = RecordingCtx()

        await _collect_with_progress(
            _agen([_Page("https://example.com/a")]), ctx, total=None, label="Deep"
        )

        assert ctx.calls[0]["total"] is None
        assert ctx.calls[0]["progress"] == 1


class TestToolsStayWired:
    """Structural guard.

    The runtime bug needs a 30-minute crawl to surface, so nothing else in
    this suite would notice a tool being 'simplified' back to a bare await.
    These assert each long-running tool still routes through a progress
    helper at all.
    """

    @pytest.mark.parametrize(
        "tool_name",
        ["crawl_many", "crawl_sitemap", "deep_crawl", "repair_browser"],
    )
    def test_long_running_tools_report_progress(self, tool_name: str) -> None:
        from crawl4ai_mcp import server as srv

        source = inspect.getsource(getattr(srv, tool_name))
        assert "_await_with_heartbeat" in source or "_collect_with_progress" in source, (
            f"{tool_name} no longer reports progress; a call outlasting the "
            "client's idle window will be aborted"
        )
