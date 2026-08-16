"""Tests for the MCP tool annotations every tool declares.

Annotations are how a client decides whether a tool needs a confirmation
prompt, so a wrong hint is a safety defect, not a cosmetic one. Each test
below pins one invariant against a regression that would actually ship:

- a new tool lands with no annotations at all, so a client sees the spec
  defaults (destructive, open-world) and nothing here is trustworthy
- a crawl tool gets marked read-only, which auto-approves the `js_code`
  parameter — arbitrary caller JavaScript in a live page, potentially inside
  an authenticated session created by create_session
- a benign tool gets marked destructive, or a genuinely destructive tool is
  added without anyone deciding it is one
- a tool that fetches a caller-supplied URL claims a closed world

The js_code and URL checks read the real tool signatures rather than a
hardcoded list, so they keep holding as tools are added or their parameters
change.
"""

import asyncio
import inspect

import pytest

from crawl4ai_mcp import server as srv


def _tools() -> list:
    """Every tool as the MCP client sees it, including generated schemas."""
    return asyncio.run(srv.mcp.list_tools())


@pytest.fixture(scope="module")
def tools() -> list:
    return _tools()


def _params(tool_name: str) -> set[str]:
    """Parameter names of the underlying handler for a registered tool."""
    fn = getattr(srv, tool_name)
    return set(inspect.signature(fn).parameters)


class TestEveryToolIsAnnotated:
    def test_all_tools_declare_annotations(self, tools) -> None:
        """A tool with annotations=None inherits the spec defaults (destructive,
        open-world), which silently misdescribes every read-only tool here."""
        unannotated = [t.name for t in tools if t.annotations is None]
        assert unannotated == [], f"tools missing annotations: {unannotated}"

    def test_all_tools_declare_a_title(self, tools) -> None:
        """Without a title, clients fall back to the raw function name in their UI."""
        untitled = [t.name for t in tools if not t.title]
        assert untitled == [], f"tools missing a title: {untitled}"

    def test_read_only_hint_is_always_explicit(self, tools) -> None:
        """read_only_hint=None is indistinguishable from 'not read-only' to a client,
        but it means nobody decided. Force the decision."""
        undecided = [t.name for t in tools if t.annotations.read_only_hint is None]
        assert undecided == [], f"tools with an undecided read_only_hint: {undecided}"


class TestJsCodeIsNeverReadOnly:
    def test_tools_accepting_js_code_are_not_read_only(self, tools) -> None:
        """js_code executes caller-supplied JavaScript inside the live page.

        Marking such a tool read-only invites a client to skip confirmation,
        which auto-approves arbitrary script execution against any URL — the
        exact hole this hint exists to gate.
        """
        offenders = [
            t.name
            for t in tools
            if "js_code" in _params(t.name) and t.annotations.read_only_hint is True
        ]
        assert offenders == [], (
            f"tools expose js_code but claim read_only_hint=True: {offenders}"
        )

    def test_the_js_code_surface_is_what_we_think_it_is(self, tools) -> None:
        """Guards the test above from silently passing on an empty set.

        If js_code were renamed, the check would match nothing and report
        clean while the hole reopened.
        """
        with_js = {t.name for t in tools if "js_code" in _params(t.name)}
        assert with_js == {
            "crawl_url",
            "crawl_many",
            "crawl_sitemap",
            "deep_crawl",
            "extract_css",
            "extract_structured",
            "extract_patterns",
        }


class TestDestructiveIsRare:
    def test_destroy_session_is_the_only_destructive_tool(self, tools) -> None:
        """Nothing else here tears anything down. A second destructive tool means
        either a mislabel or a real new capability that needs a deliberate call."""
        destructive = sorted(
            t.name for t in tools if t.annotations.destructive_hint is True
        )
        assert destructive == ["destroy_session"]

    def test_read_only_tools_leave_the_conditional_hints_unset(self, tools) -> None:
        """destructive_hint and idempotent_hint are defined as meaningful only when
        read_only_hint is false. Setting them anyway is noise a client may act on."""
        for t in tools:
            a = t.annotations
            if a.read_only_hint is True:
                assert a.destructive_hint is None, f"{t.name} sets destructive_hint"
                assert a.idempotent_hint is None, f"{t.name} sets idempotent_hint"


class TestOpenWorldMatchesReach:
    def test_tools_fetching_a_caller_supplied_url_are_open_world(self, tools) -> None:
        """A caller-supplied URL means the tool's reach is unbounded. Claiming a
        closed world there tells a client the blast radius is smaller than it is."""
        url_params = {"url", "urls", "sitemap_url"}
        offenders = [
            t.name
            for t in tools
            if _params(t.name) & url_params and t.annotations.open_world_hint is not True
        ]
        assert offenders == [], f"tools take a URL but are not open-world: {offenders}"

    def test_purely_local_tools_are_closed_world(self, tools) -> None:
        """These touch no network at all, or only two fixed endpoints the caller
        cannot steer. Flipping one to open-world would hide a new outbound call."""
        expected_closed = {
            "ping",
            "list_profiles",
            "list_sessions",
            "check_update",
            "destroy_session",
        }
        closed = {t.name for t in tools if t.annotations.open_world_hint is False}
        assert closed == expected_closed
