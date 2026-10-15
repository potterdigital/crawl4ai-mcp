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
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
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

OPT_OUT_WARNING = "CRAWL4AI_MCP_IGNORE_HTTPS_ERRORS is set"
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
                f"crawl_url {url} rejected",
                f"starts 'Crawl failed', contains {CERT_ERROR}",
                short(text),
                text.startswith("Crawl failed") and CERT_ERROR in text,
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
            f"crawl_many {SELF_SIGNED} page rejected",
            f"success false, error contains {CERT_ERROR}",
            f"success={page.get('success')} error={short(page.get('error') or '')}",
            page.get("success") is False and CERT_ERROR in (page.get("error") or ""),
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


SCENARIOS = {
    "tls": scenario_tls,
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
