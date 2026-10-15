"""Live conformance check: drive the real stdio server and check what it does.

The unit suite mocks CrawlResult, so it cannot see transport corruption, a
parameter crawl4ai silently ignores, or a page count that is off by one. This
script spawns `python -m crawl4ai_mcp.server` exactly as an MCP client would,
speaks JSON-RPC to it over stdio, and checks the answers against real sites.

Run it after any dependency bump:

    uv run python scripts/live_check.py [--out DIR] [SCENARIO ...]

With no scenario names it runs all of them. It prints a table of checks and
writes results.json into DIR, plus each server run's raw stdout
(<run>.stdout.jsonl) and stderr (<run>.stderr.log). The exit code is 0 only if
every non-informational check passed.

This is a CLIENT script, so printing the report to stdout is correct; scripts/
sits outside the ruff lint scope for that reason. Every stdout line the SERVER
writes must parse as JSON. One that does not is a transport-corruption failure
for that run, whatever the tool results were.

Adding a scenario: write a function that takes a Harness and returns nothing,
record checks with `h.check(...)`, start servers with `h.server(run_name,
env)`, and register it in SCENARIOS.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PROTOCOL_VERSION = "2025-11-25"

# Variables a scenario controls. They are stripped from the child environment
# unless the scenario sets them, so the operator's shell cannot skew a result.
CONTROLLED_ENV = (
    "CRAWL4AI_MCP_IGNORE_HTTPS_ERRORS",
    "CRAWL4AI_NO_CLOUD_NOTICE",
    "CRAWL4_AI_BASE_DIRECTORY",
    "PYTHONWARNINGS",
)

IGNORE_ENV = "CRAWL4AI_MCP_IGNORE_HTTPS_ERRORS"
OPT_OUT_WARNING = f"{IGNORE_ENV} is set"
CERT_ERROR = "net::ERR_CERT_"


@dataclass
class Check:
    scenario: str
    run: str
    name: str
    expected: str
    observed: str
    ok: bool
    informational: bool = False
    evidence: str = ""


class ServerRun:
    """One server process, driven over stdio."""

    def __init__(self, name: str, out: Path, env_overrides: dict[str, str]):
        self.name = name
        self.stdout_path = out / f"{name}.stdout.jsonl"
        self.stderr_path = out / f"{name}.stderr.log"
        env = {k: v for k, v in os.environ.items() if k not in CONTROLLED_ENV}
        env.update(env_overrides)
        self._stderr = open(self.stderr_path, "wb")
        self._stdout_log = open(self.stdout_path, "w", encoding="utf-8")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "crawl4ai_mcp.server"],
            cwd=REPO,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr,
        )
        self.lines_read = 0
        self.corrupt: list[str] = []
        self._responses: dict[int, dict] = {}
        self._cond = threading.Condition()
        self._next_id = 1
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self) -> None:
        assert self.proc.stdout is not None
        for raw in self.proc.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip("\n")
            self._stdout_log.write(line + "\n")
            self._stdout_log.flush()
            self.lines_read += 1
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                self.corrupt.append(line)
                continue
            if not isinstance(msg, dict):
                self.corrupt.append(line)
                continue
            if "method" in msg and "id" in msg:
                # A server-to-client request this client does not implement.
                self._send(
                    {
                        "jsonrpc": "2.0",
                        "id": msg["id"],
                        "error": {"code": -32601, "message": "not supported"},
                    }
                )
                continue
            if "id" in msg and ("result" in msg or "error" in msg):
                with self._cond:
                    self._responses[msg["id"]] = msg
                    self._cond.notify_all()
            # Anything else is a notification (progress, logging): tolerated.
        with self._cond:
            self._cond.notify_all()

    def _send(self, msg: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write((json.dumps(msg) + "\n").encode())
        self.proc.stdin.flush()

    def request(self, method: str, params: dict, timeout: float) -> dict:
        rid = self._next_id
        self._next_id += 1
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        with self._cond:
            while rid not in self._responses:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"{method} timed out after {timeout}s")
                if self.proc.poll() is not None:
                    raise RuntimeError(f"server exited with {self.proc.returncode}")
                self._cond.wait(min(remaining, 1.0))
            return self._responses.pop(rid)

    def initialize(self) -> None:
        self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "live_check", "version": "1"},
            },
            timeout=120,
        )
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def call(self, tool: str, args: dict, timeout: float = 240) -> dict:
        """Call a tool. Returns the JSON-RPC result, or {'_error': ...}."""
        try:
            resp = self.request(
                "tools/call", {"name": tool, "arguments": args}, timeout
            )
        except (TimeoutError, RuntimeError) as exc:
            return {"_error": str(exc)}
        if "error" in resp:
            return {"_error": json.dumps(resp["error"])}
        return resp["result"]

    def close(self, grace: float = 30) -> None:
        try:
            assert self.proc.stdin is not None
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        self._reader.join(timeout=10)
        self._stdout_log.close()
        self._stderr.close()

    def stderr_text(self) -> str:
        return self.stderr_path.read_text(encoding="utf-8", errors="replace")


def text_of(result: dict) -> str:
    """The text content of a string-returning tool's result."""
    if "_error" in result:
        return f"<call failed: {result['_error']}>"
    parts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
    return "\n".join(parts)


def structured(result: dict) -> dict:
    return result.get("structuredContent") or {}


def short(text: str, limit: int = 160) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


class Harness:
    def __init__(self, out: Path):
        self.out = out
        self.checks: list[Check] = []
        self.scenario = ""
        self.calls: dict[str, list] = {}

    def check(
        self,
        run: str,
        name: str,
        expected: str,
        observed: str,
        ok: bool,
        informational: bool = False,
        evidence: str = "",
    ) -> bool:
        self.checks.append(
            Check(self.scenario, run, name, expected, observed, bool(ok), informational, evidence)
        )
        return ok

    def server(self, run: str, env: dict[str, str] | None = None) -> ServerRun:
        srv = ServerRun(run, self.out, env or {})
        srv.initialize()
        self.calls[run] = []
        return srv

    def call(self, srv: ServerRun, tool: str, args: dict, timeout: float = 240) -> dict:
        started = time.monotonic()
        result = srv.call(tool, args, timeout)
        self.calls[srv.name].append(
            {"tool": tool, "args": args, "seconds": round(time.monotonic() - started, 1), "result": result}
        )
        return result

    def finish(self, srv: ServerRun) -> None:
        """Close a run and record the transport check that applies to every run."""
        srv.close()
        (self.out / f"{srv.name}.calls.json").write_text(
            json.dumps(self.calls.get(srv.name, []), indent=2), encoding="utf-8"
        )
        self.check(
            srv.name,
            "stdout is JSON-RPC only",
            ">=1 line, 0 non-JSON lines",
            f"{srv.lines_read} lines, {len(srv.corrupt)} non-JSON"
            + (f": {short(srv.corrupt[0], 80)}" if srv.corrupt else ""),
            srv.lines_read > 0 and not srv.corrupt,
            evidence=srv.stdout_path.name,
        )


# ── tls ─────────────────────────────────────────────────────────────────────

SELF_SIGNED = "https://self-signed.badssl.com/"
EXPIRED = "https://expired.badssl.com/"
BADSSL = "https://badssl.com/"

# Each page's own <title>, read from the first observed run. A page's title is
# filled in only when its HTML actually arrived, and the error report never
# carries one, so a match can only mean the page was fetched. Matching page
# text would not work: badssl.com's index is all links, which the content
# filter prunes to nothing, and "badssl" alone also appears in error reports.
TITLES = {
    SELF_SIGNED: "self-signed.badssl.com",
    EXPIRED: "expired.badssl.com",
    BADSSL: "badssl.com",
}

# The bad-certificate hosts serve under 50 visible characters, which crawl4ai's
# structural anti-bot heuristic (antibot_detector.py: <50 chars on a <5KB page)
# marks as blocked even after a clean HTTP 200, and it has no switch to turn
# that off. This pads the rendered body past the threshold so the success
# check measures TLS rather than that heuristic. The unpadded run is recorded
# too, as its own check.
PAD_JS = (
    "document.body.insertAdjacentHTML('beforeend', '<p>' + "
    "'live_check padding so the structural block heuristic passes. '.repeat(3) + '</p>')"
)


def page_by_url(result: dict) -> dict[str, dict]:
    return {p.get("url"): p for p in structured(result).get("pages") or []}


def check_fetched(h: Harness, run: str, url: str, page: dict, how: str) -> None:
    h.check(
        run,
        f"{how} {url} fetched",
        f"success true, HTTP 200, title {TITLES[url]!r}",
        f"success={page.get('success')} status={page.get('status_code')} "
        f"title={page.get('title')!r} error={short(page.get('error') or '', 80)}",
        page.get("success") is True
        and page.get("status_code") == 200
        and page.get("title") == TITLES[url],
        evidence=f"{run}.calls.json",
    )


def scenario_tls(h: Harness) -> None:
    run = "tls-default"
    srv = h.server(run, {"PYTHONWARNINGS": "always::DeprecationWarning"})
    try:
        for url in (SELF_SIGNED, EXPIRED):
            text = text_of(h.call(srv, "crawl_url", {"url": url}))
            h.check(
                run,
                f"crawl_url {url} rejected, report names the opt-out",
                f"starts 'Crawl failed', contains {CERT_ERROR} and {IGNORE_ENV}",
                short(text) + f" | names opt-out: {IGNORE_ENV in text}",
                text.startswith("Crawl failed") and CERT_ERROR in text and IGNORE_ENV in text,
                evidence=f"{run}.calls.json",
            )
        text = text_of(h.call(srv, "crawl_url", {"url": BADSSL}))
        h.check(
            run,
            f"crawl_url {BADSSL} not rejected",
            f"does not start 'Crawl failed', no {CERT_ERROR}",
            repr(short(text)),
            not text.startswith("Crawl failed") and CERT_ERROR not in text,
            evidence=f"{run}.calls.json",
        )
        pages = page_by_url(h.call(srv, "crawl_many", {"urls": [SELF_SIGNED, BADSSL]}))
        page = pages.get(SELF_SIGNED, {})
        h.check(
            run,
            f"crawl_many {SELF_SIGNED} page rejected, error names the opt-out",
            f"success false, error contains {CERT_ERROR} and {IGNORE_ENV}",
            f"success={page.get('success')} names opt-out: {IGNORE_ENV in (page.get('error') or '')} "
            f"error={short(page.get('error') or '')}",
            page.get("success") is False
            and CERT_ERROR in (page.get("error") or "")
            and IGNORE_ENV in (page.get("error") or ""),
            evidence=f"{run}.calls.json",
        )
        check_fetched(h, run, BADSSL, pages.get(BADSSL, {}), "crawl_many")
    finally:
        h.finish(srv)
    stderr = srv.stderr_text()
    h.check(
        run,
        "no opt-out warning on stderr",
        f"0 lines containing {OPT_OUT_WARNING!r}",
        f"{stderr.count(OPT_OUT_WARNING)} lines",
        OPT_OUT_WARNING not in stderr,
        evidence=srv.stderr_path.name,
    )

    run = "tls-optout"
    srv = h.server(run, {"CRAWL4AI_MCP_IGNORE_HTTPS_ERRORS": "1"})
    try:
        for url in (SELF_SIGNED, EXPIRED):
            text = text_of(h.call(srv, "crawl_url", {"url": url}))
            h.check(
                run,
                f"crawl_url {url} TLS accepted (unpadded)",
                f"'HTTP status: 200', no {CERT_ERROR}",
                short(text),
                "HTTP status: 200" in text and CERT_ERROR not in text,
                evidence=f"{run}.calls.json",
            )
            text = text_of(h.call(srv, "crawl_url", {"url": url, "js_code": PAD_JS}))
            h.check(
                run,
                f"crawl_url {url} succeeds (padded)",
                # The content filter prunes the page's one-line heading, so the
                # page-specific proof is the crawl_many title check below. Here
                # the marker shows the page's own DOM loaded and was returned.
                "does not start 'Crawl failed', contains the marker injected into the page",
                short(text),
                not text.startswith("Crawl failed") and "live_check padding" in text,
                evidence=f"{run}.calls.json",
            )
        pages = page_by_url(
            h.call(srv, "crawl_many", {"urls": [SELF_SIGNED, EXPIRED, BADSSL], "js_code": PAD_JS})
        )
        for url in (SELF_SIGNED, EXPIRED, BADSSL):
            check_fetched(h, run, url, pages.get(url, {}), "crawl_many (padded)")
    finally:
        h.finish(srv)
    stderr = srv.stderr_text()
    h.check(
        run,
        "opt-out warning on stderr",
        f">=1 line containing {OPT_OUT_WARNING!r}",
        f"{stderr.count(OPT_OUT_WARNING)} lines",
        OPT_OUT_WARNING in stderr,
        evidence=srv.stderr_path.name,
    )


# ── local fixture server ────────────────────────────────────────────────────

PROSE = (
    "This page exists so a crawler has something real to read. It carries a "
    "full paragraph of ordinary sentences, well past the fifty visible "
    "characters crawl4ai's structural block heuristic wants to see, so a "
    "fetch of it is judged on whether it arrived and nothing else."
)


def _page(title: str, links: list[str]) -> bytes:
    items = "".join(f'<li><a href="{href}">{href}</a></li>' for href in links)
    return (
        f"<!doctype html><html><head><title>{title}</title></head><body>"
        f"<h1>{title}</h1><p>{PROSE}</p><ul>{items}</ul></body></html>"
    ).encode()


BLOCKED_HOST = "blocked.localhost"


class Fixture:
    """A link-graph server on 127.0.0.1 that logs every request it receives.

    /g/  clean graph: root -> a..e, each -> two leaves.
    /f/  failing graph: root -> dead FIRST, then a..d, each -> one leaf.
         /f/dead closes the connection without a status line (ERR_EMPTY_RESPONSE).
    /bd/ links to one blocked host spelled three ways, plus an allowed one.
         The host is blocked.localhost, not localhost: crawl4ai's BFS
         can_process_url rejects any netloc without a dot as "Invalid domain"
         before the filter chain runs, so a bare localhost link is never
         fetched and a blocked_domains check on it would prove nothing.
         Chromium resolves every *.localhost name to loopback.
    """

    def __init__(self) -> None:
        self.log: list[tuple[str, str]] = []
        self._lock = threading.Lock()
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:  # keep the report readable
                pass

            def do_GET(self) -> None:
                with fixture._lock:
                    fixture.log.append((self.headers.get("Host", ""), self.path))
                if self.path == "/f/dead":
                    self.close_connection = True
                    return
                body = fixture.route(self.path)
                if body is None:
                    self.send_response(404)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(_page("Not found", []))
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def route(self, path: str) -> bytes | None:
        port = self.port
        if path == "/g/":
            return _page("Graph root", [f"/g/{c}" for c in "abcde"])
        if len(path) == 4 and path.startswith("/g/") and path[3] in "abcde":
            return _page(f"Graph {path[3]}", [f"{path}/1", f"{path}/2"])
        if path.startswith("/g/") and path[-2:] in ("/1", "/2"):
            return _page(f"Graph leaf {path}", [])
        if path == "/f/":
            return _page("Failing root", ["/f/dead"] + [f"/f/{c}" for c in "abcd"])
        if len(path) == 4 and path.startswith("/f/") and path[3] in "abcd":
            return _page(f"Failing {path[3]}", [f"{path}/1"])
        if path.startswith("/f/") and path.endswith("/1"):
            return _page(f"Failing leaf {path}", [])
        if path == "/bd/":
            return _page(
                "Blocked domains root",
                [
                    f"http://{BLOCKED_HOST}:{port}/bd/port",
                    f"http://{BLOCKED_HOST}.:{port}/bd/dot",
                    f"http://u@{BLOCKED_HOST}:{port}/bd/userinfo",
                    f"http://127.0.0.1:{port}/bd/allowed",
                ],
            )
        if path in ("/bd/port", "/bd/dot", "/bd/userinfo", "/bd/allowed"):
            return _page(f"Blocked domains {path}", [])
        return None

    def mark(self) -> int:
        with self._lock:
            return len(self.log)

    def paths_since(self, mark: int) -> list[str]:
        with self._lock:
            return [p for _, p in self.log[mark:]]

    def dump(self, out: Path, name: str, mark: int) -> None:
        with self._lock:
            rows = self.log[mark:]
        (out / f"{name}.requests.json").write_text(
            json.dumps([{"host": h, "path": p} for h, p in rows], indent=2), encoding="utf-8"
        )


_FIXTURE: Fixture | None = None


def fixture() -> Fixture:
    global _FIXTURE
    if _FIXTURE is None:
        _FIXTURE = Fixture()
    return _FIXTURE


# ── deep-crawl ──────────────────────────────────────────────────────────────

REAL_SITE = "https://docs.astral.sh/uv/"


def _deep(h: Harness, srv: ServerRun, args: dict) -> dict:
    base = {"cache_mode": "bypass"}
    base.update(args)
    return structured(h.call(srv, "deep_crawl", base, timeout=600))


def scenario_deep_crawl(h: Harness) -> None:
    fx = fixture()
    run = "deep-crawl"
    srv = h.server(run)
    try:
        for strategy, extra in (("bfs", {}), ("best-first", {"relevance_keywords": ["install"]})):
            for n in (1, 3, 5):
                r = _deep(h, srv, {"url": REAL_SITE, "max_depth": 2, "max_pages": n, "strategy": strategy, **extra})
                h.check(
                    run,
                    f"real site {strategy} max_pages={n}",
                    f"crawled == {n}",
                    f"crawled={r.get('crawled')} total={r.get('total')} error={r.get('error')}",
                    r.get("crawled") == n,
                    evidence=f"{run}.calls.json",
                )
        for strategy, ns in (("bfs", (1, 3, 5)), ("best-first", (3,))):
            for n in ns:
                extra = {"relevance_keywords": ["graph"]} if strategy == "best-first" else {}
                r = _deep(h, srv, {"url": f"{fx.base}/g/", "max_depth": 2, "max_pages": n, "strategy": strategy, **extra})
                h.check(
                    run,
                    f"fixture /g/ {strategy} max_pages={n}",
                    f"crawled == {n}",
                    f"crawled={r.get('crawled')} total={r.get('total')}",
                    r.get("crawled") == n,
                    evidence=f"{run}.calls.json",
                )
        mark = fx.mark()
        r = _deep(
            h, srv,
            {"url": f"{fx.base}/f/", "max_depth": 2, "max_pages": 3, "strategy": "bfs", "include_links": True},
        )
        fx.dump(h.out, "deep-crawl-f", mark)
        pages = r.get("pages") or []
        dead = [p for p in pages if str(p.get("url", "")).endswith("/f/dead")]
        requested = "/f/dead" in fx.paths_since(mark)
        h.check(
            run,
            "fixture /f/ bfs max_pages=3, one fetch fails",
            "crawled == 3, /f/dead present with success false, /f/dead requested",
            f"crawled={r.get('crawled')} total={r.get('total')} "
            f"dead={[(p.get('success'), short(p.get('error') or '', 60)) for p in dead]} "
            f"dead_requested={requested} urls={[p.get('url', '').split(str(fx.port))[-1] for p in pages]}",
            r.get("crawled") == 3 and len(dead) == 1 and dead[0].get("success") is False and requested,
            evidence="deep-crawl-f.requests.json",
        )
    finally:
        h.finish(srv)


# ── blocked-domains ─────────────────────────────────────────────────────────

BYPASS_FORMS = ("/bd/port", "/bd/dot", "/bd/userinfo")


def scenario_blocked_domains(h: Harness) -> None:
    fx = fixture()
    run = "blocked-domains"
    srv = h.server(run)
    args = {"url": f"{fx.base}/bd/", "scope": "any", "max_depth": 1, "max_pages": 10}
    try:
        mark = fx.mark()
        _deep(h, srv, args)
        fx.dump(h.out, "blocked-domains-control", mark)
        control = set(fx.paths_since(mark))
        reachable = [f for f in BYPASS_FORMS if f in control]
        for form in BYPASS_FORMS:
            h.check(
                run,
                f"control: {form} reachable",
                "requested with no blocked_domains",
                "requested" if form in control else "unreachable in control, not evidence",
                form in control,
                informational=form != "/bd/port",
                evidence="blocked-domains-control.requests.json",
            )

        mark = fx.mark()
        _deep(h, srv, {**args, "blocked_domains": [BLOCKED_HOST]})
        fx.dump(h.out, "blocked-domains-blocked", mark)
        blocked = set(fx.paths_since(mark))
        leaked = [f for f in reachable if f in blocked]
        h.check(
            run,
            f"blocked_domains [{BLOCKED_HOST!r}] fetches no form of it",
            f"0 of {reachable} requested",
            f"requested: {leaked or 'none'}",
            "/bd/port" in reachable and not leaked,
            evidence="blocked-domains-blocked.requests.json",
        )
        h.check(
            run,
            "blocked run still fetches /bd/allowed",
            "/bd/allowed requested",
            "requested" if "/bd/allowed" in blocked else "missing",
            "/bd/allowed" in blocked,
            evidence="blocked-domains-blocked.requests.json",
        )
    finally:
        h.finish(srv)


# ── notice ──────────────────────────────────────────────────────────────────

BANNER = "Crawl4AI Cloud is live"
DEPRECATION = "PruningContentFilter (BeautifulSoup-based) is deprecated"


def scenario_notice(h: Harness) -> None:
    fx = fixture()
    # crawl4ai shows the banner at most once a day per home folder, so each run
    # gets a fresh one; otherwise "absent" could just mean "shown earlier today".
    for run, env, want_banner in (
        ("notice-control", {"CRAWL4AI_NO_CLOUD_NOTICE": "0"}, True),
        ("notice-default", {"PYTHONWARNINGS": "always::DeprecationWarning"}, False),
    ):
        home = Path(tempfile.mkdtemp(prefix=f"live_check-{run}-"))
        srv = h.server(run, {**env, "CRAWL4_AI_BASE_DIRECTORY": str(home)})
        try:
            text = text_of(h.call(srv, "crawl_url", {"url": f"{fx.base}/g/"}))
            h.check(
                run,
                "crawl_url fixture /g/ succeeds",
                "does not start 'Crawl failed'",
                short(text, 80),
                bool(text) and not text.startswith("Crawl failed"),
                evidence=f"{run}.calls.json",
            )
        finally:
            h.finish(srv)
            shutil.rmtree(home, ignore_errors=True)
        stderr = srv.stderr_text()
        if want_banner:
            h.check(
                run,
                "banner shown when the user sets CRAWL4AI_NO_CLOUD_NOTICE=0 (positive control)",
                f"stderr contains {BANNER!r}",
                f"{stderr.count(BANNER)} occurrences",
                BANNER in stderr,
                evidence=srv.stderr_path.name,
            )
        else:
            h.check(
                run,
                "banner silenced by default",
                f"stderr lacks {BANNER!r}",
                f"{stderr.count(BANNER)} occurrences",
                BANNER not in stderr,
                evidence=srv.stderr_path.name,
            )
            h.check(
                run,
                "no PruningContentFilter deprecation warning",
                f"stderr lacks {DEPRECATION!r}",
                f"{stderr.count(DEPRECATION)} occurrences",
                DEPRECATION not in stderr,
                evidence=srv.stderr_path.name,
            )


# ── upstream-bfs (informational) ────────────────────────────────────────────


def scenario_upstream_bfs(h: Harness) -> None:
    """Drive crawl4ai's own BFS strategy, no MCP, to re-measure the off-by-one."""
    os.environ.setdefault("CRAWL4AI_NO_CLOUD_NOTICE", "1")
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
    from crawl4ai.deep_crawling import BFSDeepCrawlStrategy

    fx = fixture()
    url = f"{fx.base}/g/"

    async def measure() -> dict[tuple[bool, int], int]:
        counts: dict[tuple[bool, int], int] = {}
        async with AsyncWebCrawler(config=BrowserConfig(headless=True, verbose=False)) as crawler:
            for stream in (False, True):
                for n in (1, 3, 5):
                    cfg = CrawlerRunConfig(
                        deep_crawl_strategy=BFSDeepCrawlStrategy(max_depth=2, max_pages=n),
                        cache_mode=CacheMode.BYPASS,
                        verbose=False,
                        stream=stream,
                    )
                    out = await crawler.arun(url=url, config=cfg)
                    if stream:
                        results = [r async for r in out]
                    else:
                        results = list(out)
                    counts[(stream, n)] = sum(1 for r in results if r.success)
        return counts

    counts = asyncio.run(measure())
    for stream in (False, True):
        row = "  ".join(f"{n}->{counts[(stream, n)]}" for n in (1, 3, 5))
        h.check(
            "upstream-bfs",
            f"BFSDeepCrawlStrategy stream={stream}",
            "max_pages -> successful results",
            row,
            True,
            informational=True,
        )


# ── filter-parity ───────────────────────────────────────────────────────────

PARITY_PAGES = (
    REAL_SITE,
    "https://docs.astral.sh/uv/guides/projects/",
    "https://docs.python.org/3/library/asyncio-task.html",
)


def scenario_filter_parity(h: Harness) -> None:
    """Measure the default content filter against crawl4ai's lxml replacement.

    crawl4ai 0.9.4 deprecated PruningContentFilter for PruningContentFilterLXML,
    but the lxml class takes no preserve_tags, which this server relies on to
    keep code blocks. This records what a switch would cost on real docs pages
    (informational) and checks that the server's own default filter, as
    build_run_config builds it, still produces the code-preserving output.
    """
    import warnings

    import httpx
    from crawl4ai import PruningContentFilter, PruningContentFilterLXML

    from crawl4ai_mcp.profiles import ProfileManager, build_run_config

    kwargs = dict(threshold=0.48, threshold_type="fixed", min_word_threshold=10)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        reference = PruningContentFilter(**kwargs, preserve_tags=["pre", "code"])
    lxml = PruningContentFilterLXML(**kwargs)
    server_default = build_run_config(ProfileManager(), None).markdown_generator.content_filter

    rows = []
    for url in PARITY_PAGES:
        html = httpx.get(url, timeout=30, follow_redirects=True).text
        ref = "".join(reference.filter_content(html))
        new = "".join(lxml.filter_content(html))
        ours = "".join(server_default.filter_content(html))
        row = {
            "url": url,
            "html_bytes": len(html),
            "default_chars": len(ref),
            "default_code": ref.count("<code"),
            "lxml_chars": len(new),
            "lxml_code": new.count("<code"),
            "server_default_matches": ours == ref,
        }
        rows.append(row)
        h.check(
            "filter-parity",
            f"server default filter keeps code blocks: {url}",
            "build_run_config's filter output == PruningContentFilter(preserve_tags=[pre, code])",
            f"identical={ours == ref} ({len(ours)} vs {len(ref)} chars)",
            ours == ref,
            evidence="filter-parity.json",
        )
        h.check(
            "filter-parity",
            f"cost of switching to PruningContentFilterLXML: {url}",
            "chars and <code> kept: default vs lxml",
            f"chars {row['default_chars']} vs {row['lxml_chars']}, "
            f"<code> {row['default_code']} vs {row['lxml_code']}",
            True,
            informational=True,
            evidence="filter-parity.json",
        )
    (h.out / "filter-parity.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")


SCENARIOS = {
    "tls": scenario_tls,
    "deep-crawl": scenario_deep_crawl,
    "blocked-domains": scenario_blocked_domains,
    "notice": scenario_notice,
    "upstream-bfs": scenario_upstream_bfs,
    "filter-parity": scenario_filter_parity,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, help="evidence directory (default: a temp dir)")
    parser.add_argument("scenarios", nargs="*", help=f"any of: {', '.join(SCENARIOS)}")
    args = parser.parse_args()

    names = args.scenarios or list(SCENARIOS)
    unknown = [n for n in names if n not in SCENARIOS]
    if unknown:
        parser.error(f"unknown scenario(s): {', '.join(unknown)}")
    out = args.out or Path(tempfile.mkdtemp(prefix="live_check-"))
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=True)

    h = Harness(out)
    for name in names:
        h.scenario = name
        try:
            SCENARIOS[name](h)
        except Exception as exc:  # a crashed scenario is a failed check, not a traceback
            h.check(name, "scenario ran", "no exception", f"{type(exc).__name__}: {exc}", False)

    (out / "results.json").write_text(
        json.dumps([asdict(c) for c in h.checks], indent=2), encoding="utf-8"
    )
    failed = [c for c in h.checks if not c.ok and not c.informational]
    for c in h.checks:
        status = "INFO" if c.informational else ("PASS" if c.ok else "FAIL")
        print(f"[{status}] {c.scenario}/{c.run}: {c.name}")
        print(f"         expected: {c.expected}")
        print(f"         observed: {c.observed}")
    print(f"\n{len(h.checks)} checks, {len(failed)} failed. Evidence: {out}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
