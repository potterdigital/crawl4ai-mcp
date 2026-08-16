# src/crawl4ai_mcp/server.py
import asyncio
import contextvars
import gzip
import hashlib
import importlib.metadata
import json
import logging
import os
import re
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from urllib.parse import urljoin

# MUST be first: configure all logging to stderr before any library imports emit output.
# Any output to stdout corrupts the MCP stdio JSON-RPC transport.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from crawl4ai import (
    AsyncWebCrawler,
    BrowserConfig,
    CacheMode,
    CrawlerRunConfig,
    JsonCssExtractionStrategy,
    JsonXPathExtractionStrategy,
    LLMConfig,
    LLMExtractionStrategy,
    RegexExtractionStrategy,
)
from crawl4ai.async_dispatcher import RateLimiter, SemaphoreDispatcher
from crawl4ai.deep_crawling import (
    BestFirstCrawlingStrategy,
    BFSDeepCrawlStrategy,
    FilterChain,
    URLPatternFilter,
)
from crawl4ai.deep_crawling.filters import DomainFilter
from crawl4ai.deep_crawling.scorers import KeywordRelevanceScorer
import httpx
from mcp.server.mcpserver import Context, MCPServer
from mcp.types import ToolAnnotations
from packaging.version import Version
from pydantic import BaseModel

from crawl4ai_mcp.profiles import (
    ProfileManager,
    build_run_config,
    effective_profile_keys,
)


AUTO_REPAIR_ENV = "CRAWL4AI_MCP_AUTO_REPAIR"
BROWSER_INSTALL_TIMEOUT_S = 1800

# Serializes browser installs so a background repair and a repair_browser call
# can never run two downloads into the same cache directory at once.
_repair_lock = asyncio.Lock()


@dataclass
class BrowserState:
    """Readiness of the Chromium browser the crawler needs.

    status is one of:
      ready     — crawler is live and tools can run
      repairing — an automatic `crawl4ai-setup` install is in flight
      failed    — no browser, and no repair running; detail says why

    This exists because the browser can be missing for a reason the server can
    fix itself (Playwright upgraded and its matching Chromium build was never
    downloaded). Exiting on that condition hides the cause: the MCP client
    reports only that the server failed to connect, and the actionable stderr
    message is buried in a connect log nobody reads.
    """

    status: str = "ready"
    detail: str = ""
    started_at: float = 0.0


def _auto_repair_enabled() -> bool:
    """Automatic browser install is on unless explicitly disabled."""
    return os.environ.get(AUTO_REPAIR_ENV, "1").strip().lower() not in {
        "0",
        "false",
        "no",
    }


def _chromium_status() -> tuple[bool, str]:
    """Report whether the Chromium build Playwright expects is present on disk.

    Returns (ok, detail). Never raises — a broken Playwright install is itself
    a reportable state, not a crash.
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        return False, f"Playwright is not importable in this environment: {e}"

    try:
        with sync_playwright() as p:
            exe = p.chromium.executable_path
            if not exe or not Path(exe).exists():
                return False, f"Chromium build not found at {exe or '<unknown path>'}"
            return True, exe
    except Exception as e:
        return False, str(e)


def _install_browser() -> tuple[bool, str]:
    """Download the Chromium build Playwright expects. Blocking; call via to_thread.

    Prefers the `crawl4ai-setup` console script from this venv because it is the
    documented fix and also covers patchright and crawl4ai's local DB init.
    Falls back to `python -m playwright install chromium`, which fixes the
    browser itself, when that script is absent.

    Output is captured rather than inherited: anything a child writes to our
    stdout would corrupt the MCP JSON-RPC stream.
    """
    setup_script = Path(sys.executable).parent / "crawl4ai-setup"
    if setup_script.exists():
        cmd = [str(setup_script)]
    else:
        cmd = [sys.executable, "-m", "playwright", "install", "chromium"]

    logger.info("Installing browser via: %s", " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=BROWSER_INSTALL_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return False, f"browser install timed out after {BROWSER_INSTALL_TIMEOUT_S}s"
    except Exception as e:
        return False, f"browser install could not be launched: {e}"

    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-600:]
        return False, f"browser install exited {proc.returncode}: {tail}"

    ok, detail = _chromium_status()
    if not ok:
        return (
            False,
            f"install reported success but Chromium is still missing: {detail}",
        )
    return True, detail


def _build_browser_config() -> BrowserConfig:
    """Browser settings shared by initial startup and any later repair."""
    # No extra_args: crawl4ai's BrowserManager already hardcodes --disable-gpu,
    # --disable-dev-shm-usage and --no-sandbox, and dedupes. Verified the launch
    # arg list is identical with and without them, so passing them was inert.
    #
    # Worth keeping out rather than keeping "just in case": crawl4ai's managed
    # browser path drops --disable-gpu deliberately when stealth is enabled,
    # because disabling the GPU kills WebGL and anti-bot sensors read that as
    # headless. Re-adding it here would silently undo that if stealth is ever
    # turned on.
    return BrowserConfig(
        headless=True,
        verbose=False,  # CRITICAL: verbose=True outputs to stdout, corrupting MCP transport
    )


async def _start_crawler() -> tuple[AsyncWebCrawler | None, str]:
    """Create and start a crawler. Returns (crawler, error_detail)."""
    crawler = AsyncWebCrawler(config=_build_browser_config())
    try:
        await crawler.start()
        # Installed once, for the crawler's lifetime. They read per-call data
        # from a ContextVar, so they must never be set or cleared per call.
        _install_override_hooks(crawler)
        return crawler, ""
    except Exception as e:
        try:
            await crawler.close()
        except Exception:
            pass
        return None, str(e)


async def _repair_browser(app_ctx: "AppContext") -> tuple[bool, str]:
    """Install the browser, then bring the crawler up. Mutates app_ctx in place.

    Safe to call concurrently: the lock means a second caller waits for the
    install in flight and then observes the resulting state rather than
    starting a competing download.
    """
    async with _repair_lock:
        if app_ctx.crawler is not None:
            return True, "browser already ready"

        app_ctx.browser.status = "repairing"
        app_ctx.browser.started_at = time.time()

        ok, detail = await asyncio.to_thread(_install_browser)
        if not ok:
            app_ctx.browser.status = "failed"
            app_ctx.browser.detail = detail
            logger.error("Browser repair failed: %s", detail)
            return False, detail

        crawler, err = await _start_crawler()
        if crawler is None:
            app_ctx.browser.status = "failed"
            app_ctx.browser.detail = err
            logger.error("Browser installed but crawler failed to start: %s", err)
            return False, err

        app_ctx.crawler = crawler
        app_ctx.browser.status = "ready"
        app_ctx.browser.detail = ""
        logger.info("Browser repaired — crawler is operational")
        return True, "browser installed and crawler started"


def _require_crawler(app: "AppContext") -> AsyncWebCrawler:
    """Return the live crawler, or raise an error that says how to fix it.

    MCPServer surfaces the exception text to the calling agent, so this is what
    turns a dead browser into something the caller can act on in one step
    instead of a cryptic AttributeError on None.
    """
    if app.crawler is not None:
        return app.crawler

    state = app.browser
    if state.status == "repairing":
        elapsed = int(time.time() - state.started_at)
        raise RuntimeError(
            f"Browser not ready: Chromium is installing automatically ({elapsed}s elapsed). "
            "Retry this call shortly, or call repair_browser to wait for the install to finish."
        )

    raise RuntimeError(
        "Browser unavailable — Playwright's Chromium build is missing or failed to launch. "
        "Call the repair_browser tool to install it, or run `uv run crawl4ai-setup` in the "
        f"crawl4ai-mcp project. Details: {state.detail or 'unknown'}"
    )


@dataclass
class AppContext:
    """Typed lifespan context shared across all tool calls.

    The crawler is a single AsyncWebCrawler instance created at server startup
    and reused for every tool call. This avoids the 2-5 second Chromium startup
    cost on every request and prevents browser process leaks.

    profile_manager holds all loaded YAML profiles and is used by build_run_config
    to construct CrawlerRunConfig instances with profile + per-call merging.

    sessions maps session_id strings to their creation timestamp (seconds since
    epoch). Sessions are persistent browser pages that preserve cookies,
    localStorage, and DOM state across crawl_url calls.

    crawler is None only while the browser is unavailable (missing Chromium
    build, or a failed launch). The server deliberately stays up in that state
    so the failure is visible through ping and the tool errors, instead of the
    process exiting and the MCP client showing a bare "failed to connect".

    browser carries that readiness state and the remediation detail.
    """

    crawler: AsyncWebCrawler | None
    profile_manager: ProfileManager
    sessions: dict[str, float]
    browser: "BrowserState" = field(default_factory=lambda: BrowserState())


@asynccontextmanager
async def app_lifespan(server: MCPServer) -> AsyncIterator[AppContext]:
    """Initialize AsyncWebCrawler once at server startup; close at shutdown.

    Uses explicit crawler.start() / crawler.close() rather than `async with
    AsyncWebCrawler()` because the lifespan function is itself the context manager.
    The finally block guarantees cleanup even if a tool raises an unhandled exception.
    """
    logger.info("crawl4ai MCP server starting — initializing browser")

    crawler, err = await _start_crawler()
    state = BrowserState()
    if crawler is not None:
        logger.info("Browser ready — crawl4ai MCP server is operational")
    else:
        state.status = "failed"
        state.detail = err
        logger.error("Browser failed to start: %s", err)

    profile_manager = ProfileManager()
    logger.info(
        "Loaded %d profile(s): %s", len(profile_manager.names), profile_manager.names
    )

    # Fire-and-forget version check — never blocks server readiness
    asyncio.create_task(_startup_version_check())

    app_ctx = AppContext(
        crawler=crawler,
        profile_manager=profile_manager,
        sessions={},
        browser=state,
    )

    # A missing browser is repairable, so repair it — but in the background.
    # MCP_TIMEOUT bounds server STARTUP (its documented example is 10 seconds),
    # while tool calls get a far longer budget. Downloading ~150MB of Chromium
    # here would blow the handshake and the client would report only a connect
    # failure, which is the exact silent failure this design removes. So the
    # transport opens immediately and readiness is reported through the tools.
    if crawler is None and _auto_repair_enabled():
        state.status = "repairing"
        state.started_at = time.time()
        logger.info("Auto-repair enabled — installing browser in the background")
        asyncio.create_task(_repair_browser(app_ctx))
    elif crawler is None:
        logger.error(
            "Auto-repair disabled via %s — call the repair_browser tool or run "
            "`uv run crawl4ai-setup`",
            AUTO_REPAIR_ENV,
        )

    try:
        yield app_ctx
    finally:
        # Read app_ctx.crawler, not the local: a repair may have replaced it.
        live = app_ctx.crawler
        if live is not None:
            # Clean up active sessions before closing browser
            for sid in list(app_ctx.sessions.keys()):
                try:
                    await live.crawler_strategy.kill_session(sid)
                except Exception:
                    pass
            logger.info("Shutting down browser")
            await live.close()
        logger.info("Shutdown complete")


mcp = MCPServer("crawl4ai", lifespan=app_lifespan)


# --- Tool annotation policy -------------------------------------------------
#
# Every tool declares MCP ToolAnnotations so a client can reason about safety
# before invoking it. The hints are spec-defined (see the ToolAnnotations type
# in the MCP schema). The SDK exposes them under snake_case names since 2.0;
# the spec and the JSON on the wire still call them readOnlyHint,
# destructiveHint, idempotentHint, and openWorldHint. The non-obvious calls
# made here are:
#
# read_only_hint=False on every crawl and extract tool. These look like pure
#   retrieval, and mostly are, but they accept a `js_code` parameter that runs
#   caller-supplied JavaScript inside the live page. A client that skips
#   confirmation for read-only tools would then be auto-approving arbitrary
#   script execution against any URL — including against an authenticated
#   session created via create_session. The retrieval framing is not worth that
#   hole, so these are not marked read-only.
#
# destructive_hint=False on those same tools. This is where the useful signal
#   lives: they only ever add (a cache entry, a session page, files under
#   output_dir). Nothing the server owns is torn down. destroy_session is the
#   single exception and the only tool marked destructive.
#
# open_world_hint=False on check_update. It does reach the network, but only two
#   fixed endpoints (PyPI and the crawl4ai changelog on GitHub). The caller
#   cannot steer the target, so its world is closed. The crawl tools take a
#   caller-supplied URL and are open.
#
# Local cache writes are deliberately NOT treated as "modifies its environment".
# Every HTTP client caches; counting that would make no tool read-only and
# drain the hint of meaning.
#
# Per the spec these are hints, not guarantees, and clients must treat
# annotations from untrusted servers as untrusted.
# ---------------------------------------------------------------------------

PROVIDER_ENV_VARS: dict[str, str | None] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "groq": "GROQ_API_KEY",
    "ollama": None,  # local, no key needed
}


DEFAULT_PAGE_TIMEOUT_S = 60

_CACHE_MAP = {
    "enabled": CacheMode.ENABLED,
    "bypass": CacheMode.BYPASS,
    "disabled": CacheMode.DISABLED,
    "read_only": CacheMode.READ_ONLY,
    "write_only": CacheMode.WRITE_ONLY,
}

# Crawling fresh is the default, and that is a deliberate reversal.
#
# crawl4ai's cache stores the RAW page and does not preserve the filtered
# markdown, so reading from it silently discards every content control this
# server exists to apply. Measured on one docs page with a BM25 query: the
# first crawl returned 1,848 characters of the relevant sections, and an
# identical second crawl served from cache returned 21,767 characters of the
# whole page. The cached result also carries status_code=None, which this
# server documents as meaning "no response was ever received".
#
# So under the old default of "enabled", crawling any page a second time
# quietly returned different, twelve-times-larger, wrongly-labelled content.
# Nothing in the response said so. Speed is not worth an answer that changes
# depending on whether you happened to have visited the page before; callers
# who want the cache can still ask for it by name.
DEFAULT_CACHE_MODE = "bypass"


def _bad_choice(param: str, value: object, valid: list[str]) -> str:
    """The message every unrecognised enum value gets."""
    return f"{param} {value!r} is not recognised. Valid values: {', '.join(sorted(valid))}."


def _resolve_cache_mode(cache_mode: str | None) -> tuple[CacheMode, str | None]:
    """Map the caller's cache_mode to crawl4ai's enum, or explain the refusal.

    Returns (mode, error). An unrecognised value is REFUSED rather than quietly
    downgraded to the default. The old behaviour logged a warning to stderr,
    which an MCP client never sees, so `cache_mode="bypas"` silently ran with
    caching on and the caller had no way to learn that the setting they asked
    for was not the setting they got. Same reasoning as `selector_type` on
    extract_css.
    """
    if cache_mode is None:
        return _CACHE_MAP[DEFAULT_CACHE_MODE], None
    key = cache_mode.strip().lower()
    if key in _CACHE_MAP:
        return _CACHE_MAP[key], None
    return _CACHE_MAP[DEFAULT_CACHE_MODE], _bad_choice(
        "cache_mode", cache_mode, list(_CACHE_MAP)
    )


def _one_line(text: object, limit: int = 200) -> str:
    """Collapse a value to a single truncated line fit for an error message.

    crawl4ai puts raw exception text in its block reasons, and a Playwright
    navigation failure carries a multi-line call log. Pasted verbatim into a
    diagnostic that is already several lines long, one of those buries
    everything around it.
    """
    flattened = " ".join(str(text).split())
    if len(flattened) <= limit:
        return flattened
    return flattened[: limit - 1] + "…"


def _failure_diagnostics(url: str, result) -> list[str]:
    """Explain WHY a crawl failed, using fields crawl4ai already populates.

    Returns extra lines to append to an error report, or [] when there is
    nothing non-obvious to say. Every source here is filled by crawl4ai on its
    own; none of it costs an extra request.

    What it surfaces, and why each one earns its place:

    - `redirected_url`, when it differs from what was asked for. A crawl that
      failed somewhere other than where it was pointed is a different problem
      from one that failed at the target, and a login wall is the usual reason.
      crawl4ai sets this field even when nothing redirected, so the comparison
      is what makes it meaningful rather than noise on every error.
    - `Retry-After`, when the server sent one. On a 429 this is the single most
      actionable header there is, and `response_headers` is captured before the
      block check, so it survives onto a result marked failed.
    - `crawl_stats`, when it shows retries or blocking. crawl4ai retries behind
      anti-bot detection on its own, so "blocked" can mean one refusal or five,
      possibly across proxies and an HTTP fallback. Without this the caller sees
      a single terse message and cannot tell a hard block from a flaky one.

    Everything is read defensively. `crawl_stats` is None on a plain
    single-attempt connection failure -- the retry loop re-raises rather than
    building a result when there is one proxy and one attempt -- which is the
    most common failure of all, so absence here is normal, not exceptional.
    """
    lines: list[str] = []

    redirected = getattr(result, "redirected_url", None)
    if isinstance(redirected, str) and redirected and redirected != url:
        code = getattr(result, "redirected_status_code", None)
        suffix = f" (HTTP {code})" if isinstance(code, int) else ""
        lines.append(f"Redirected to: {redirected}{suffix}")

    headers = getattr(result, "response_headers", None)
    if isinstance(headers, dict):
        retry_after = next(
            (v for k, v in headers.items() if str(k).lower() == "retry-after"), None
        )
        if retry_after:
            lines.append(f"Retry-After: {_one_line(retry_after, 60)}")

    stats = getattr(result, "crawl_stats", None)
    if not isinstance(stats, dict):
        return lines

    attempts_log = stats.get("proxies_used")
    attempts_log = attempts_log if isinstance(attempts_log, list) else []
    blocked = [a for a in attempts_log if isinstance(a, dict) and a.get("blocked")]
    retries = stats.get("retries")
    retries = retries if isinstance(retries, int) else 0

    # Stay quiet on a clean single failure: repeating "1 attempt, 0 retries"
    # on every error would drown the cases where the numbers mean something.
    if not blocked and retries <= 0:
        return lines

    total = stats.get("attempts")
    total = total if isinstance(total, int) else len(attempts_log)
    detail = f"Attempts: {total}"
    if retries > 0:
        detail += f" ({retries} retried after a block)"
    if blocked:
        detail += f", {len(blocked)} blocked"
    lines.append(detail)

    # Reasons repeat across attempts far more often than they differ, so dedupe
    # rather than printing the same sentence once per retry.
    reasons: list[str] = []
    for attempt in blocked:
        reason = _one_line(attempt.get("reason") or "")
        if reason and reason not in reasons:
            reasons.append(reason)
    for reason in reasons[:3]:
        lines.append(f"Blocked by: {reason}")

    if stats.get("fallback_fetch_used"):
        resolved = stats.get("resolved_by")
        outcome = "succeeded" if resolved == "fallback_fetch" else "also failed"
        lines.append(f"crawl4ai's plain-HTTP fallback was tried and {outcome}.")

    return lines


def _format_crawl_error(url: str, result) -> str:
    """Convert a failed CrawlResult into a structured error string for Claude.

    This pattern is used by all crawl tools in subsequent phases. Returning a
    structured string (rather than raising) lets Claude reason about the failure
    and decide how to proceed.
    """
    lines = [
        "Crawl failed",
        f"URL: {url}",
        f"HTTP status: {result.status_code}",
        f"Error: {result.error_message}",
    ]
    lines.extend(_failure_diagnostics(url, result))
    return "\n".join(lines)


def _extraction_error(extracted_content: str | None) -> str | None:
    """Return the LLM failure buried in extracted_content, or None if it is data.

    crawl4ai's LLMExtractionStrategy reports a failed completion in-band: the
    strategy still "succeeds" and extracted_content becomes a list of blocks
    flagged {"error": true}. Only the flag is reliable -- the message text
    varies by provider -- so match on that and never on the word "error",
    which appears in perfectly good extracted data.
    """
    if not extracted_content:
        return None
    try:
        parsed = json.loads(extracted_content)
    except (json.JSONDecodeError, TypeError):
        return None

    blocks = parsed if isinstance(parsed, list) else [parsed]
    messages = [
        str(b.get("content") or "").strip()
        for b in blocks
        if isinstance(b, dict) and b.get("error") is True
    ]
    if not messages:
        return None
    return " | ".join(m for m in messages if m) or "the provider reported an error"


def _check_api_key(provider: str) -> str | None:
    """Validate that the expected API key env var is set for the given provider.

    Returns a structured error string if the key is missing or the provider is
    not one this server knows, or None if the call is safe to attempt.
    """
    prefix = provider.split("/")[0].lower()
    if prefix not in PROVIDER_ENV_VARS:
        # An unknown prefix used to be waved through on the assumption that
        # litellm would reject it. It does not: litellm treats an unrecognised
        # provider as an OpenAI-compatible model name and sends the request to
        # OPENAI. So `provider="gemin/gemini-2.5-flash"` billed OpenAI for a
        # model nobody asked for, and the caller saw an OpenAIException naming a
        # vendor they had not mentioned. A typo must not choose a vendor.
        return (
            f"Unknown provider {provider!r}\n"
            f"Known providers: {', '.join(sorted(PROVIDER_ENV_VARS))}\n"
            f"Use the form '<provider>/<model>', e.g. 'gemini/gemini-2.5-flash'. "
            f"Unrecognised names are refused rather than attempted, because "
            f"litellm would otherwise route them to OpenAI and bill that account."
        )

    env_var = PROVIDER_ENV_VARS[prefix]
    if env_var is None:
        # Provider is local (ollama) — no env var to check
        return None
    if not os.environ.get(env_var):
        return (
            f"API key not set\n"
            f"Provider: {provider}\n"
            f"Required environment variable: {env_var}\n"
            f"Set it with: export {env_var}=your-key-here"
        )
    return None


class PageLink(BaseModel):
    """One hyperlink found on a crawled page."""

    href: str
    text: str | None = None
    """The link's visible text, when it has any. Empty for image and icon links."""
    title: str | None = None
    """The link's title attribute, when it has one."""


class PageLinks(BaseModel):
    """A page's outgoing links, split the way crawl4ai splits them.

    The internal/external split compares registrable domains, so a link to a
    subdomain counts as internal. Same rule `deep_crawl`'s scope uses.
    """

    internal: list[PageLink] = []
    external: list[PageLink] = []


class PageTable(BaseModel):
    """One HTML table crawl4ai judged to be real tabular data.

    crawl4ai scores every <table> and keeps those above its threshold, so
    layout tables are already filtered out and this is not simply every table
    tag on the page.
    """

    headers: list[str] = []
    """Column headers. Empty when the table has no header row."""
    rows: list[list[str]] = []
    """Body rows, each a list of cell strings aligned to headers."""
    caption: str | None = None
    """The table's <caption>, when it has one."""


class PageResult(BaseModel):
    """One page from a multi-page crawl."""

    url: str
    success: bool
    """Whether the page was retrieved at all.

    NOT whether the server was happy: crawl4ai reports success for any page it
    managed to fetch, so an HTTP 404 or 500 arrives with success=True and the
    error page's body as markdown. Check status_code before treating content
    as real. Filtering on success alone will quietly mix error pages into the
    results.
    """
    status_code: int | None = None
    """HTTP status. None when the request never got a response at all."""
    title: str | None = None
    """The page's <title>. crawl4ai always scrapes this; it used to be discarded."""
    description: str | None = None
    """The page's meta description, when it has one."""
    markdown: str | None = None
    """Page content. None on failure, and None when output_dir wrote it to disk."""
    error: str | None = None
    """Why the page failed. None on success."""
    depth: int | None = None
    """Links away from the start URL. deep_crawl only."""
    parent_url: str | None = None
    """The page this one was discovered from. deep_crawl only."""
    file: str | None = None
    """Filename written under output_dir. None unless output_dir was set."""
    links: PageLinks | None = None
    """Outgoing links. None unless include_links was set on the call."""
    tables: list[PageTable] | None = None
    """Tabular data found on the page. None unless include_tables was set."""


class CrawlBatchResult(BaseModel):
    """Result of a multi-page crawl.

    Always reports successes and failures together: an individual URL failing
    never discards the pages that worked, so a partial crawl stays usable.
    """

    crawled: int
    """Pages that succeeded."""
    total: int
    """Pages attempted."""
    pages: list[PageResult]
    output_dir: str | None = None
    """Directory the pages were written to, when output_dir was set."""
    manifest: str | None = None
    """Path to manifest.json, when output_dir was set."""
    note: str | None = None
    """Anything the caller should know, e.g. that a sitemap was truncated."""
    error: str | None = None
    """Why the crawl produced nothing at all.

    Set only when the whole operation failed before any page was attempted --
    an unreachable or unparseable sitemap, say. A failure affecting one page
    is reported on that page, not here, so a partial crawl leaves this None.
    """


class ExtractionResult(BaseModel):
    """Result of a CSS-selector extraction."""

    url: str
    count: int
    """Number of items matched by the schema's baseSelector."""
    items: list[dict]
    """Extracted records, shaped by the caller's schema. Empty when nothing matched."""
    error: str | None = None
    """Why extraction produced nothing. None on success."""


def _clean_str(value: object) -> str | None:
    """Return a stripped string, or None when there is nothing in it.

    crawl4ai fills absent link text and titles with "" rather than leaving
    them out, and an empty string is worth nothing to a caller while still
    costing a field in every one of a thousand links.
    """
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _page_links(raw: object) -> PageLinks:
    """Project crawl4ai's link dicts down to the three fields worth carrying.

    crawl4ai attaches seven more per link -- base_domain, head_data,
    head_extraction_status, head_extraction_error and three scores -- which are
    None or 0.0 unless link-head extraction or a scorer ran, neither of which
    this server enables. Measured on a Wikipedia article, dropping them takes
    the payload from 317KB to 132KB for the same 997 links.

    No deduplication: crawl4ai already returns each href once (verified on
    Wikipedia, 997 links and 997 distinct hrefs), so deduping here would only
    be a lossy transform with nothing to gain.
    """
    links = raw if isinstance(raw, dict) else {}
    projected: dict[str, list[PageLink]] = {}
    for kind in ("internal", "external"):
        entries = links.get(kind)
        entries = entries if isinstance(entries, list) else []
        projected[kind] = [
            PageLink(
                href=entry["href"],
                text=_clean_str(entry.get("text")),
                title=_clean_str(entry.get("title")),
            )
            for entry in entries
            if isinstance(entry, dict) and _clean_str(entry.get("href"))
        ]
    return PageLinks(internal=projected["internal"], external=projected["external"])


def _page_tables(raw: object) -> list[PageTable]:
    """Project crawl4ai's extracted tables onto the model.

    Drops the `metadata` block, whose useful members (row_count, column_count)
    are len() of what is already here and whose remainder is the table's raw
    id and class attributes.
    """
    tables = raw if isinstance(raw, list) else []
    out: list[PageTable] = []
    for table in tables:
        if not isinstance(table, dict):
            continue
        headers = table.get("headers")
        rows = table.get("rows")
        out.append(
            PageTable(
                headers=[str(h) for h in headers] if isinstance(headers, list) else [],
                rows=[
                    [str(cell) for cell in row]
                    for row in (rows if isinstance(rows, list) else [])
                    if isinstance(row, list)
                ],
                caption=_clean_str(table.get("caption")),
            )
        )
    return out


def _page_results(
    results: list,
    include_content: bool = True,
    include_links: bool = False,
    include_tables: bool = False,
) -> list[PageResult]:
    """Convert crawl4ai CrawlResult objects into the wire model.

    Shared by crawl_many, deep_crawl, and crawl_sitemap. Successes come first
    so the useful half of a partial crawl is not buried under failures.

    links and tables are populated by crawl4ai on every crawl and were
    discarded here until they were asked for, because both can dwarf the page
    content: one Wikipedia article carries 997 internal links. They are opt-in
    per call rather than always-on for that reason.
    """
    pages: list[PageResult] = []
    for result in sorted(results, key=lambda r: not r.success):
        meta = result.metadata if isinstance(result.metadata, dict) else {}
        if result.success:
            md = result.markdown
            content = (md.fit_markdown or md.raw_markdown) if md else ""
            pages.append(
                PageResult(
                    url=result.url,
                    success=True,
                    status_code=result.status_code,
                    title=meta.get("title"),
                    description=meta.get("description"),
                    markdown=content if include_content else None,
                    depth=meta.get("depth"),
                    parent_url=meta.get("parent_url"),
                    links=(
                        _page_links(getattr(result, "links", None))
                        if include_links
                        else None
                    ),
                    tables=(
                        _page_tables(getattr(result, "tables", None))
                        if include_tables
                        else None
                    ),
                )
            )
        else:
            # Same diagnostics the single-page tools report. A batch is where
            # they matter most: crawling 50 URLs and having 10 come back
            # "Blocked by anti-bot protection" says nothing about whether the
            # host throttled once or refused every retry.
            error = "\n".join(
                [result.error_message or "", *_failure_diagnostics(result.url, result)]
            ).strip()
            pages.append(
                PageResult(
                    url=result.url,
                    success=False,
                    status_code=result.status_code,
                    title=meta.get("title"),
                    error=error or None,
                    depth=meta.get("depth"),
                    parent_url=meta.get("parent_url"),
                )
            )
    return pages


def _batch_result(
    results: list,
    note: str | None = None,
    include_links: bool = False,
    include_tables: bool = False,
) -> CrawlBatchResult:
    """Build the structured result returned by every multi-page crawl tool."""
    return CrawlBatchResult(
        crawled=sum(1 for r in results if r.success),
        total=len(results),
        pages=_page_results(
            results, include_links=include_links, include_tables=include_tables
        ),
        note=note,
    )


def _sanitize_filename(url: str) -> str:
    """Convert a URL to a safe, collision-free filename stem (no extension).

    The readable part is lossy on purpose: scheme stripped, non-alphanumerics
    collapsed to underscores, truncated. A hash of the FULL original URL is
    appended so that lossiness can never merge two pages into one file.

    That suffix is load-bearing, not decoration. Without it the readable part
    alone silently destroyed data in a batch, all of it reported as success:

    - every non-Latin path collapsed to nothing, because the character class
      is ASCII-only. Four distinct Chinese URLs all became "example_cn", so a
      crawl of any Chinese, Japanese, Korean, Arabic or Cyrillic site wrote
      every page over the same file and kept only the last one.
    - "/docs/api" and "/docs/api/" produced the same stem.
    - two URLs sharing their first 200 characters produced the same stem.
    - "/Page" and "/page" differ as strings but are the same file on a
      case-insensitive filesystem, which is the macOS default.

    Hashing the URL before any lossy transform closes all four at once.
    crawl4ai does the same thing in its own download path rather than
    trusting a slug to be unique.
    """
    name = re.sub(r"^https?://", "", url)
    name = re.sub(r"[^a-zA-Z0-9]+", "_", name)
    name = name.strip("_")[:180] or "page"
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:8]
    return f"{name}_{digest}"


def _output_dir_failed_note(output_dir: str, exc: OSError, note: str | None) -> str:
    """Explain a failed write without discarding the crawl it belonged to."""
    detail = (
        f"Could not write to output_dir {output_dir!r} ({exc.strerror or exc}). "
        f"The crawl itself succeeded, so the page content is returned inline "
        f"below instead of being written to disk."
    )
    return f"{note} {detail}" if note else detail


def _persist_results(
    results: list,
    output_dir: str,
    note: str | None = None,
    include_links: bool = False,
    include_tables: bool = False,
) -> CrawlBatchResult:
    """Write per-page .md files and a manifest.json to output_dir.

    Returns the same CrawlBatchResult shape as an inline crawl, with each
    page's `file` set and its `markdown` left None: the content is on disk,
    and repeating it in the result would defeat the point of output_dir.

    Requested links and tables still come back inline. Only the markdown has a
    file to be written to; inventing a second on-disk format for the structured
    data would leave the caller parsing files to get what they just asked for.
    """
    # A disk failure must not destroy the crawl.
    #
    # This used to let OSError out of the tool, so an unwritable output_dir
    # answered a successful crawl of N pages with
    # "Error executing tool crawl_many: [Errno 13] Permission denied".
    # The network work was already done and every page was in hand; a
    # permissions problem on one directory threw all of it away, and the
    # caller could not even see which pages had succeeded. It also broke this
    # server's own rule that tools never raise for an expected failure, and a
    # directory you cannot write to is an expected failure.
    #
    # The content now comes back inline instead, which is exactly what the
    # caller would have got had they not asked for output_dir at all.
    try:
        os.makedirs(output_dir, exist_ok=True)
    except OSError as exc:
        return _batch_result(
            results,
            note=_output_dir_failed_note(output_dir, exc, note),
            include_links=include_links,
            include_tables=include_tables,
        )

    successes = [r for r in results if r.success]
    failures = [r for r in results if not r.success]
    manifest_entries: list[dict] = []

    try:
        for result in successes:
            stem = _sanitize_filename(result.url)
            filename = f"{stem}.md"
            filepath = os.path.join(output_dir, filename)

            md = result.markdown
            content = (md.fit_markdown or md.raw_markdown) if md else ""
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)

            entry: dict = {"url": result.url, "file": filename, "success": True}
            if result.metadata and isinstance(result.metadata, dict):
                if "depth" in result.metadata:
                    entry["depth"] = result.metadata["depth"]
                if "parent_url" in result.metadata:
                    entry["parent_url"] = result.metadata["parent_url"]
            manifest_entries.append(entry)

        for result in failures:
            manifest_entries.append(
                {
                    "url": result.url,
                    "success": False,
                    "error": result.error_message,
                }
            )

        manifest_path = os.path.join(output_dir, "manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest_entries, f, indent=2)
    except OSError as exc:
        return _batch_result(
            results,
            note=_output_dir_failed_note(output_dir, exc, note),
            include_links=include_links,
            include_tables=include_tables,
        )

    # Same shape as an inline crawl, but pointing at files instead of carrying
    # content. include_content=False is what leaves markdown None.
    pages = _page_results(
        results,
        include_content=False,
        include_links=include_links,
        include_tables=include_tables,
    )
    by_url = {e["url"]: e for e in manifest_entries if e.get("success")}
    for page in pages:
        entry = by_url.get(page.url)
        if entry:
            page.file = entry["file"]

    return CrawlBatchResult(
        crawled=len(successes),
        total=len(results),
        pages=pages,
        output_dir=output_dir,
        manifest=manifest_path,
        note=note,
    )


# Zero-width and BOM characters seen inside real <loc> elements. They survive
# .strip() and produce a URL that looks right and does not resolve.
_INVISIBLE_CHARS = "​‌‍﻿⁠"

# Bounds sitemap-index recursion. A sitemap index that references itself, or two
# that reference each other, would otherwise recurse until the process dies.
MAX_SITEMAP_DEPTH = 5


def _clean_loc(text: str | None, base_url: str) -> str | None:
    """Normalize one <loc> value, or None if there is nothing usable in it.

    Strips invisible characters and resolves relative paths against the sitemap
    that contained them. The sitemap protocol requires absolute URLs, but
    relative ones appear in the wild and are unusable if passed through as-is.
    """
    if not text:
        return None
    cleaned = text.strip().strip(_INVISIBLE_CHARS).strip()
    if not cleaned:
        return None
    return urljoin(base_url, cleaned)


# The two bytes every gzip stream starts with. Checking these is what makes
# decompression depend on what actually arrived rather than on the URL.
_GZIP_MAGIC = b"\x1f\x8b"


def _maybe_gunzip(content: bytes) -> bytes:
    """Decompress only if the bytes really are gzip, whatever the URL said.

    This used to key off `sitemap_url.endswith(".gz")`, which is a claim about
    the request rather than the response, and the two disagree in both
    directions:

    - httpx transparently decodes `Content-Encoding: gzip`, so a server that
      sets that header on a .gz path hands us PLAIN bytes at a .gz URL.
      `gzip.decompress` then raised `BadGzipFile: Not a gzipped file (b'<?')`,
      which no caller caught, so the whole tool crashed on a sitemap that was
      perfectly valid. Same crash when a .gz URL redirects to plain XML.
    - A .xml URL that redirects to genuinely gzipped content was never
      decompressed at all, and the caller was told it "is not valid sitemap
      XML" -- pointing at the sitemap, when the sitemap was fine.

    Both disappear once the bytes decide. A decompression that still fails is
    returned as-is so the XML parser can produce the error the caller can act
    on, rather than a gzip error about a file they never said was gzipped.
    """
    if not content.startswith(_GZIP_MAGIC):
        return content
    try:
        return gzip.decompress(content)
    except (OSError, EOFError) as exc:  # BadGzipFile is an OSError subclass
        logger.warning("Sitemap looked gzipped but would not decompress: %s", exc)
        return content


async def _fetch_sitemap_urls(
    sitemap_url: str, _depth: int = 0, _seen: set[str] | None = None
) -> list[str]:
    """Fetch and parse a sitemap XML, returning all <loc> URLs.

    Handles:
    - Regular sitemaps (<urlset> with <url><loc>)
    - Sitemap indexes (<sitemapindex> with <sitemap><loc>) -- resolved concurrently
    - Gzipped sitemaps (.xml.gz) -- automatically decompressed
    - Any sitemap XML namespace, or none

    Namespaces are matched with ElementTree's `{*}` wildcard rather than the
    sitemaps.org 0.9 URI. Pinning that URI meant a sitemap declaring any other
    namespace -- the older google.com/schemas/sitemap/0.84 among them -- parsed
    to zero URLs and was reported to the caller as an empty sitemap.
    """
    seen = _seen if _seen is not None else set()
    seen.add(sitemap_url)

    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        resp = await client.get(sitemap_url)
        resp.raise_for_status()

    content = _maybe_gunzip(resp.content)

    root = ET.fromstring(content)
    # Resolve relative <loc> against the final URL after redirects, not the one
    # requested, so a sitemap that redirected resolves against where it landed.
    base_url = str(resp.url) if getattr(resp, "url", None) else sitemap_url

    # A sitemap index: <sitemapindex><sitemap><loc>
    sub_locs = [
        loc for elem in root.findall("{*}sitemap") for loc in elem.findall("{*}loc")
    ]
    if sub_locs:
        if _depth >= MAX_SITEMAP_DEPTH:
            logger.warning(
                "Sitemap index nesting exceeded %d levels at %s -- not descending further",
                MAX_SITEMAP_DEPTH,
                sitemap_url,
            )
            return []

        targets = []
        for loc in sub_locs:
            child = _clean_loc(loc.text, base_url)
            if child and child not in seen:
                seen.add(child)
                targets.append(child)

        # Concurrent rather than sequential: a large index can reference
        # hundreds of sub-sitemaps, and fetching them one at a time dominates
        # the runtime of the whole crawl.
        results = await asyncio.gather(
            *(_fetch_sitemap_urls(t, _depth + 1, seen) for t in targets),
            return_exceptions=True,
        )
        urls: list[str] = []
        for target, result in zip(targets, results):
            if isinstance(result, BaseException):
                # One bad sub-sitemap must not discard the others.
                logger.warning("Sub-sitemap %s failed: %s", target, result)
                continue
            urls.extend(result)
        return urls

    # A regular sitemap: <urlset><url><loc>
    locs = [loc for elem in root.findall("{*}url") for loc in elem.findall("{*}loc")]
    cleaned = (_clean_loc(loc.text, base_url) for loc in locs)
    return [u for u in cleaned if u]


async def _get_latest_pypi_version() -> tuple[str, dict]:
    """Query PyPI for the latest crawl4ai release version.

    Returns a tuple of (version_string, full_json_data) from PyPI's JSON API.
    Raises httpx.HTTPError or httpx.TimeoutException on failure (caller handles).
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get("https://pypi.org/pypi/crawl4ai/json")
        resp.raise_for_status()
        data = resp.json()
        return data["info"]["version"], data


async def _fetch_changelog_summary(version: str) -> str:
    """Fetch and extract changelog highlights for a specific crawl4ai version.

    Fetches CHANGELOG.md from the crawl4ai GitHub repo and extracts the section
    for the given version. Returns category headers and first-level bullets,
    truncated to 20 lines.

    On any failure, returns a fallback URL string pointing to the changelog.
    """
    # Distinguish "could not read the changelog" from "upstream never wrote an
    # entry for this version". The second is the common case, not an edge case:
    # crawl4ai tagged and shipped 0.9.1 and 0.9.2 to PyPI without adding either
    # to CHANGELOG.md, so this lookup silently fell through to a bare link for
    # the two most recent releases and the caller could not tell that the tool
    # had tried and found nothing.
    fallback = "Changelog: https://github.com/unclecode/crawl4ai/blob/main/CHANGELOG.md"
    no_entry = (
        f"No changelog entry for {version} upstream — crawl4ai does not always "
        f"add one for a release. See the release notes:\n"
        f"https://github.com/unclecode/crawl4ai/releases/tag/v{version}"
    )
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://raw.githubusercontent.com/unclecode/crawl4ai/main/CHANGELOG.md"
            )
            resp.raise_for_status()

        text = resp.text
        # Extract the section for this version
        pattern = rf"## \[{re.escape(version)}\].*?\n(.*?)(?=\n## \[|$)"
        match = re.search(pattern, text, re.DOTALL)
        if not match:
            return no_entry

        section = match.group(1)
        # Keep category headers (### ) and first-level bullets (- **)
        lines = []
        for line in section.splitlines():
            stripped = line.strip()
            if stripped.startswith("### ") or stripped.startswith("- **"):
                lines.append(stripped)
        if not lines:
            return no_entry

        # Truncate to 20 lines
        if len(lines) > 20:
            lines = lines[:20]
            lines.append("... (truncated)")

        return "\n".join(lines)
    except Exception:
        return fallback


async def _startup_version_check() -> None:
    """Fire-and-forget check for crawl4ai updates at server startup.

    Logs a warning to stderr if a newer version is available on PyPI.
    Uses a tighter 5-second timeout. This function MUST NEVER raise —
    version checking should never disrupt server startup.
    """
    try:
        installed = importlib.metadata.version("crawl4ai")
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get("https://pypi.org/pypi/crawl4ai/json")
            resp.raise_for_status()
            data = resp.json()
            latest = data["info"]["version"]

        if Version(latest) > Version(installed):
            logger.warning(
                "A newer crawl4ai version is available: %s (installed: %s). "
                "Run scripts/update.sh to upgrade.",
                latest,
                installed,
            )
    except Exception:
        pass  # Never disrupt server startup


# Per-call header/cookie payload for the hooks below. A ContextVar is scoped to
# the running task, so two overlapping tool calls each see their own value.
#
# This must NOT be plain state on the strategy. crawl4ai keeps one hook slot per
# hook type on the strategy instance, and this server shares a single crawler
# across every tool call, so writing a call's headers into that slot published
# them to every other in-flight call: an overlapping crawl of a different host
# received the first call's Authorization header, and whichever call finished
# first cleared the slot out from under the other.
_call_overrides: contextvars.ContextVar[dict] = contextvars.ContextVar(
    "crawl4ai_call_overrides", default={}
)


async def _override_before_goto(page, context, url, config, **kwargs):
    """Apply this task's headers. Installed once; a no-op when none are set."""
    headers = _call_overrides.get().get("headers")
    if headers:
        await page.set_extra_http_headers(headers)


async def _override_on_context(page, context, **kwargs):
    """Apply this task's cookies. Installed once; a no-op when none are set."""
    cookies = _call_overrides.get().get("cookies")
    if cookies:
        await context.add_cookies(cookies)


def _install_override_hooks(crawler: AsyncWebCrawler) -> None:
    """Install the override hooks once, at startup."""
    crawler.crawler_strategy.set_hook("before_goto", _override_before_goto)
    crawler.crawler_strategy.set_hook("on_page_context_created", _override_on_context)


async def _clear_injected_cookies(crawler: AsyncWebCrawler, cookies: list) -> None:
    """Remove cookies this call injected, so they do not outlive it.

    add_cookies writes into the browser context, and crawl4ai caches and reuses
    contexts. Closing the page does not close the context, so an injected
    session cookie survived into every later crawl of the same domain: a call
    that passed no cookies at all was still sending the previous call's
    credentials, with no way to clear them short of restarting the server.
    """
    try:
        manager = crawler.crawler_strategy.browser_manager
    except AttributeError:  # pragma: no cover - upstream layout change
        return

    # crawl4ai caches a context per config signature and also keeps a default
    # one. Which of them served this crawl depends on the config, so clear from
    # every live context rather than guessing; clearing a cookie that was never
    # there is a no-op.
    contexts = []
    default = getattr(manager, "default_context", None)
    if default is not None:
        contexts.append(default)
    contexts.extend(getattr(manager, "contexts_by_config", {}).values())

    for context in contexts:
        clear = getattr(context, "clear_cookies", None)
        if clear is None:  # pragma: no cover - a Browser, not a BrowserContext
            continue
        for cookie in cookies:
            if not isinstance(cookie, dict):
                continue
            name = cookie.get("name")
            if not name:
                continue
            # Scope the clear to the cookie we actually injected.
            #
            # This used to call clear(name=name) with no domain, which clears
            # that name in EVERY context. Cookie names are not unique to a
            # caller -- "session", "sid", "auth_token" and "session_token" are
            # exactly what everyone reaches for -- so one call's cleanup would
            # silently delete a live session's identically-named cookie and
            # de-authenticate a workflow midway through. Narrowing by domain
            # and path means cleanup only removes what this call put there.
            criteria: dict = {"name": name}
            if cookie.get("domain"):
                criteria["domain"] = cookie["domain"]
            if cookie.get("path"):
                criteria["path"] = cookie["path"]
            try:
                await clear(**criteria)
            except Exception as exc:  # pragma: no cover - playwright drift
                logger.warning("Could not clear injected cookie %r: %s", name, exc)


async def _crawl_with_overrides(
    crawler: AsyncWebCrawler,
    url: str,
    config: CrawlerRunConfig,
    headers: dict | None = None,
    cookies: list | None = None,
):
    """Run arun with per-request header and cookie injection.

    CrawlerRunConfig still has no headers or cookies parameters in crawl4ai
    0.9.2 (verified against the installed signature; they exist only on
    BrowserConfig, which is global), so per-request injection still has to go
    through Playwright hooks. What changed is where the per-call data lives:
    in a ContextVar keyed to this task rather than in a slot on the shared
    strategy. See _call_overrides.

    Injected cookies are cleared afterwards unless the call is part of a named
    session, where persisting them across calls is the entire point.
    """
    token = _call_overrides.set({"headers": headers, "cookies": cookies})
    try:
        return await crawler.arun(url=url, config=config)
    finally:
        _call_overrides.reset(token)
        if cookies and not getattr(config, "session_id", None):
            await _clear_injected_cookies(crawler, cookies)


# Seconds between heartbeats for work that reports no per-item progress.
# Comfortably under any client idle window while staying far below the
# "rate limit progress notifications" guidance in the MCP spec.
PROGRESS_HEARTBEAT_S = 15.0


async def _emit_progress(
    ctx: "Context[AppContext]",
    progress: float,
    total: float | None = None,
    message: str | None = None,
) -> None:
    """Send a progress notification, swallowing any failure.

    Progress is telemetry about the work, so a notification that cannot be
    delivered must never take down the work itself. report_progress is
    already a no-op when the client did not opt in with a progressToken.
    """
    try:
        await ctx.report_progress(progress=progress, total=total, message=message)
    except Exception as exc:  # pragma: no cover - transport-level failure
        logger.debug("progress notification failed: %s", exc)


async def _collect_with_progress(
    stream,
    ctx: "Context[AppContext]",
    total: int | None,
    label: str,
) -> list:
    """Drain a streaming crawl into a list, reporting each completed page.

    Why this exists: a client aborts a tool call that sends neither a
    response nor a progress notification for its idle window — 30 minutes
    for stdio in Claude Code. A deep crawl used to be a single awaited call
    that put nothing on the wire until every page was done, so a crawl that
    ran past the window was killed outright and the work was lost. Reporting
    each page resets that timer, and it is the only reason deep_crawl runs
    in streaming mode rather than awaiting the batch.

    Used only by deep_crawl. The two arun_many-based tools heartbeat instead,
    because streaming there would require swapping the dispatcher; see the
    note at that call site.
    """
    results: list = []
    async for result in stream:
        results.append(result)
        done = len(results)
        suffix = f"/{total}" if total else ""
        await _emit_progress(
            ctx,
            progress=done,
            total=total,
            message=f"{label}: {done}{suffix} pages ({result.url})",
        )
    return results


async def _await_with_heartbeat(
    coro,
    ctx: "Context[AppContext]",
    label: str,
    interval: float = PROGRESS_HEARTBEAT_S,
):
    """Await an opaque long operation, emitting a progress heartbeat while it runs.

    For work that cannot report per-item progress — a browser install is one
    opaque subprocess — a heartbeat is what keeps the client's idle timer
    from firing. The progress value counts heartbeats, which satisfies the
    spec requirement that progress increase on every notification.
    """
    task = asyncio.ensure_future(coro)
    started = time.time()
    ticks = 0
    while True:
        done, _pending = await asyncio.wait({task}, timeout=interval)
        if done:
            return task.result()
        ticks += 1
        await _emit_progress(
            ctx,
            progress=ticks,
            message=f"{label} ({int(time.time() - started)}s elapsed)",
        )


@mcp.tool(
    title="Server health check",
    annotations=ToolAnnotations(
        read_only_hint=True,
        open_world_hint=False,  # inspects in-process state only
    ),
)
async def ping(ctx: Context[AppContext]) -> str:
    """Verify the MCP server is running and the browser is ready.

    Returns 'ok' if the server is healthy. If the browser is missing or still
    installing, returns a description of that state and how to resolve it —
    the server stays reachable in those states by design, so this is the tool
    that tells you why crawling is unavailable.
    """
    try:
        app: AppContext = ctx.request_context.lifespan_context
        if app.crawler is not None:
            return "ok"

        state = app.browser
        if state.status == "repairing":
            elapsed = int(time.time() - state.started_at)
            return (
                f"degraded: Chromium is installing automatically ({elapsed}s elapsed). "
                "Crawl tools will work once it finishes; call repair_browser to wait on it."
            )
        return (
            "error: browser unavailable — Playwright's Chromium build is missing or "
            "failed to launch. Call repair_browser to install it, or run "
            f"`uv run crawl4ai-setup`. Details: {state.detail or 'unknown'}"
        )
    except Exception as e:
        logger.error("ping failed: %s", e, exc_info=True)
        return f"error: {e}"


@mcp.tool(
    title="Install and recover the browser",
    annotations=ToolAnnotations(
        read_only_hint=False,  # writes ~150MB of Chromium to the Playwright cache
        destructive_hint=False,  # additive install; never removes an existing build
        idempotent_hint=True,  # no-op when the browser is already healthy
        open_world_hint=True,  # downloads from Playwright's CDN
    ),
)
async def repair_browser(ctx: Context[AppContext]) -> str:
    """Install the Chromium build the crawler needs, then bring the browser up.

    Use when ping reports the browser is unavailable. This runs the same
    install as `uv run crawl4ai-setup` (a ~150MB download on a cold cache) and
    then starts the crawler, so crawling recovers without restarting the
    server. Safe to call when the browser is already healthy — it is a no-op.

    If a background repair is already running, this waits for it rather than
    starting a second download.
    """
    try:
        app: AppContext = ctx.request_context.lifespan_context
        if app.crawler is not None:
            return "ok: browser already ready"

        # The install can run for up to BROWSER_INSTALL_TIMEOUT_S (1800s), which
        # is exactly Claude Code's default stdio idle window. Awaiting it silently
        # means a slow ~150MB download races the client's abort and can lose to
        # it, so heartbeat while it runs.
        ok, detail = await _await_with_heartbeat(
            _repair_browser(app), ctx, "Installing Chromium"
        )
        if ok:
            return f"ok: {detail}"
        return (
            f"error: repair failed — {detail}\n"
            "Try `uv run crawl4ai-setup` in the crawl4ai-mcp project and check its output."
        )
    except Exception as e:
        logger.error("repair_browser failed: %s", e, exc_info=True)
        return f"error: {e}"


@mcp.tool(
    title="List crawl profiles",
    annotations=ToolAnnotations(
        read_only_hint=True,
        open_world_hint=False,  # reads profiles loaded into memory at startup
    ),
)
async def list_profiles(ctx: Context[AppContext]) -> str:
    """List all available crawl profiles and their configuration settings.

    Profiles provide named starting-point configurations for crawl_url.
    Per-call parameters always override profile values (merge order: default -> profile -> per-call).

    The 'default' profile is a special base layer automatically applied to every crawl,
    even when no profile is specified. All named profiles are merged on top of 'default'.

    To use a custom profile: create a YAML file in the profiles/ directory
    (e.g. profiles/my_profile.yaml) and pass profile='my_profile' to crawl_url.
    Custom profiles are picked up on next server restart.
    """
    app: AppContext = ctx.request_context.lifespan_context
    profiles = app.profile_manager.all()
    if not profiles:
        return "No profiles loaded. Check that src/crawl4ai_mcp/profiles/ directory exists."

    lines = []
    for name in sorted(profiles):
        cfg = profiles[name]
        if name == "default":
            lines.append(f"## {name} (base layer — applied to every crawl)")
        else:
            lines.append(f"## {name}")
        if not cfg:
            lines.append("  (no settings — inherits all defaults)")
        else:
            # Show what actually applies. This used to print the raw YAML, so a
            # key that build_run_config strips on the next line was reported to
            # the caller as an active setting -- the tool that exists to say
            # what a profile does was describing settings that do nothing.
            applied, ignored = effective_profile_keys(cfg)
            for k, v in sorted(applied.items()):
                lines.append(f"  {k}: {v}")
            if ignored:
                lines.append(
                    "  IGNORED (not valid crawl4ai settings, these have no effect): "
                    + ", ".join(sorted(ignored))
                )
        lines.append("")  # blank line between profiles

    return "\n".join(lines).rstrip()


@mcp.tool(
    title="Check for a crawl4ai update",
    annotations=ToolAnnotations(
        read_only_hint=True,  # reports only; the upgrade itself is scripts/update.sh
        open_world_hint=False,  # two fixed endpoints; the caller cannot steer them
    ),
)
async def check_update(ctx: Context[AppContext]) -> str:
    """Check if a newer version of crawl4ai is available on PyPI.

    Compares the installed version against the latest release. Reports version
    info and changelog highlights. Never performs the upgrade itself -- use
    scripts/update.sh for that.
    """
    installed = importlib.metadata.version("crawl4ai")

    try:
        latest, _data = await _get_latest_pypi_version()
    except (httpx.HTTPError, httpx.TimeoutException) as exc:
        return (
            f"Version check failed\n"
            f"Installed: {installed}\n"
            f"Error: Could not reach PyPI ({exc})"
        )
    except Exception as exc:
        return f"Version check failed\nInstalled: {installed}\nError: {exc}"

    if Version(latest) <= Version(installed):
        return f"crawl4ai is up to date\nInstalled: {installed}\nLatest: {latest}"

    # Update available — fetch changelog summary
    changelog = await _fetch_changelog_summary(latest)

    return (
        f"Update available\n"
        f"Installed: {installed}\n"
        f"Latest: {latest}\n"
        f"Release: https://github.com/unclecode/crawl4ai/releases/tag/v{latest}\n"
        f"To upgrade: stop the server and run: scripts/update.sh\n"
        f"\n"
        f"Changelog highlights:\n{changelog}"
    )


@mcp.tool(
    title="Crawl a URL to markdown",
    annotations=ToolAnnotations(
        read_only_hint=False,  # js_code runs caller JS in-page; session_id persists state
        destructive_hint=False,  # additive only: a cache entry and possibly a session
        idempotent_hint=False,  # js_code may have side effects on each call
        open_world_hint=True,  # fetches a caller-supplied URL
    ),
)
async def crawl_url(
    url: str,
    profile: str | None = None,
    session_id: str | None = None,
    query: str | None = None,
    cache_mode: str | None = None,
    css_selector: str | None = None,
    target_elements: list[str] | None = None,
    excluded_selector: str | None = None,
    wait_for: str | None = None,
    js_code: str | None = None,
    user_agent: str | None = None,
    headers: dict | None = None,
    cookies: list | None = None,
    page_timeout: int | None = None,
    word_count_threshold: int | None = None,
    ctx: Context[AppContext] = None,
) -> str:
    """Crawl a URL and return clean, filtered markdown content.

    By default, applies PruningContentFilter to produce fit_markdown — a
    noise-reduced version of the page with navigation bars, footers, and
    low-density blocks removed. Falls back to raw_markdown if fit_markdown
    is not available.

    Args:
        url: The URL to crawl.

        profile: Name of a built-in or custom crawl profile to use as the base
            configuration for this request. Per-call parameters take precedence
            over profile values. Available profiles: "fast", "js_heavy", "stealth".
            If None (default), only the "default" profile base is applied.
            Use list_profiles to see all available profiles and their settings.

        session_id: Optional session name for persistent browser state. When
            provided, the crawl reuses the same browser page across calls —
            cookies, localStorage, and DOM state persist. First call with a new
            session_id creates the session automatically. Use create_session to
            set up a session with initial cookies before crawling. Sessions have
            a 30-minute inactivity TTL.

        query: Filter the page to the parts relevant to this question, before
            any tokens are spent. Swaps the default density-based pruning for
            crawl4ai's BM25 scoring, which ranks each block against the query.
            Measured on a docs page: 9,278 characters became 2,165, and the
            retained text was the sections that answered the question. No LLM
            and no cost. Leave unset to keep the density filter.

        cache_mode: Controls crawl4ai's cache read/write behaviour.
            - "enabled"    — use cache if available, fetch and store on miss (default)
            - "bypass"     — always fetch fresh; do not read or write cache
            - "disabled"   — fetch fresh; no cache read or write for this session
            - "read_only"  — return cached result only; fail if not cached
            - "write_only" — fetch fresh and overwrite cache; ignore existing cached

        css_selector: Restrict extraction to elements matching this CSS selector
            (include scope). Example: "article.main-content" extracts only the
            article element. Without this, the full page body is extracted.

            This narrows the DOCUMENT, not just the output, so everything read
            from outside your selector is lost with it: the page's title and
            meta description live in <head> and come back None, and links are
            restricted to those inside the scope. Even css_selector="body"
            drops the title. Prefer target_elements when you want the same
            markdown without giving those up.

        target_elements: Restrict the MARKDOWN to these CSS selectors while
            leaving the rest of the page readable. Takes a list.

            Use this rather than css_selector when you want a focused page body
            but still want the title, description, or links. Measured on the
            same page and selector: both produce identical markdown, but
            css_selector returned title=None and 0 links where target_elements
            returned the real title and all 25 links.

        excluded_selector: Exclude elements matching this CSS selector from
            extraction (exclude noise). Example: "nav, footer, .sidebar" removes
            navigation, footer, and sidebar elements before generating markdown.

        wait_for: Wait until a CSS selector or JavaScript condition is met before
            extracting content. Useful for pages with dynamic content.
            Format:
            - CSS: "css:#main-content" — wait until #main-content exists in DOM
            - JS:  "js:() => window.dataLoaded === true" — wait until JS expression is truthy

        js_code: JavaScript to execute in the page after load and before extraction.
            Use this to trigger lazy loading, click buttons, or scroll to load more.
            Examples:
            - Single string: "window.scrollTo(0, document.body.scrollHeight);"
            - Note: pass as string; crawl4ai handles execution in the page context.

        user_agent: Override the browser User-Agent string.

            Not reliably per-request, despite the name: crawl4ai applies it by
            mutating the shared browser config, and browser contexts are cached
            and reused. In practice the FIRST user_agent used for a context wins
            for that context's lifetime, later calls passing a different one are
            ignored, and calls passing none inherit the previous value. Use it
            when every crawl in a run wants the same agent; do not rely on it to
            vary per call. Example: "Mozilla/5.0 (compatible; MyBot/1.0)"

        headers: Dict of custom HTTP headers to send with the request. Applied
            via Playwright page hooks, which are page-scoped, so a header does
            not reach any other call.

            EXCEPT inside a session: a session reuses one page across calls, so
            a header set on the first call of a session keeps being sent on
            every later call of that same session. It does not escape to
            non-session calls. Pass headers per call if that is not what you
            want.
            Example: {"Authorization": "Bearer token", "X-Custom": "val"}

        cookies: List of cookie dicts to send with the request. Each cookie must
            have at minimum: name, value, domain. Optional fields: path, expires,
            httpOnly, secure, sameSite.
            Example: [{"name": "session", "value": "abc123", "domain": "example.com"}]

            SECURITY, please read before sending a real credential. Cookies are
            not confined to the call that supplied them the way headers are.
            Playwright stores cookies on the browser CONTEXT, and crawl4ai keeps
            one shared context per browser configuration, so an injected cookie
            is visible to any other crawl using that context while it is there.
            Concretely, all of these are true today and are not bugs this server
            can fix, because crawl4ai 0.9.2 offers no per-call or per-session
            cookie storage:

            - Cookies passed WITHOUT session_id are cleared when the call ends,
              so the exposure lasts only as long as the call. A crawl running
              concurrently with it can still see them.
            - Cookies passed WITH session_id are deliberately kept, since that
              is what a session is for. They stay in the shared jar for the life
              of the session (30-minute idle TTL) and are therefore sent on
              other crawls of the same domain, including crawls that pass no
              cookies and no session_id at all.
            - Two named sessions share that jar, so a credential in one session
              is sent on the other session's requests to that domain.

            Cookies do stay scoped to their own domain, so this is a same-domain
            exposure, not one host's credential reaching another host.
            destroy_session clears a session's cookies immediately and is the
            way to end the exposure early. If you need two identities against
            one site kept genuinely apart, run them through separate server
            processes rather than separate sessions.

        page_timeout: Maximum seconds to wait for the page to load before timing
            out (default 60). Converted to milliseconds internally.

        word_count_threshold: Minimum word count for a content block to survive
            PruningContentFilter (default 10). Lower values retain more short
            blocks; higher values prune more aggressively.
    """
    resolved_cache, cache_error = _resolve_cache_mode(cache_mode)
    if cache_error:
        return cache_error

    logger.info("crawl_url: %s (cache=%s, profile=%s)", url, cache_mode, profile)

    # Build per-call kwargs — only include optional params when explicitly set
    # so that profile values are not silently overridden by None/default sentinel values.
    # Convert page_timeout from seconds (tool interface) to ms (CrawlerRunConfig native unit).
    per_call_kwargs: dict = {"cache_mode": resolved_cache}
    if page_timeout is not None:
        per_call_kwargs["page_timeout"] = page_timeout * 1000
    if css_selector is not None:
        per_call_kwargs["css_selector"] = css_selector
    if target_elements is not None:
        per_call_kwargs["target_elements"] = target_elements
    if excluded_selector is not None:
        per_call_kwargs["excluded_selector"] = excluded_selector
    if wait_for is not None:
        per_call_kwargs["wait_for"] = wait_for
    if js_code is not None:
        per_call_kwargs["js_code"] = js_code
    if user_agent is not None:
        per_call_kwargs["user_agent"] = user_agent
    if session_id is not None:
        per_call_kwargs["session_id"] = session_id
    if word_count_threshold is not None:
        per_call_kwargs["word_count_threshold"] = word_count_threshold
    if query is not None:
        per_call_kwargs["query"] = query

    app: AppContext = ctx.request_context.lifespan_context
    run_cfg = build_run_config(app.profile_manager, profile, **per_call_kwargs)

    result = await _crawl_with_overrides(
        _require_crawler(app), url, run_cfg, headers, cookies
    )

    # Register the session on ANY outcome, not just success.
    #
    # crawl4ai registers the session in its own browser_manager during page
    # setup, before navigation, so a failed crawl still leaves a live session
    # holding whatever cookies were injected. Registering only on success left
    # that session invisible to list_sessions and unreachable by
    # destroy_session: the caller could see neither that it existed nor any way
    # to tear it down, and destroy_session is the only thing that clears a
    # session's cookies.
    if session_id and session_id not in app.sessions:
        app.sessions[session_id] = time.time()

    if not result.success:
        return _format_crawl_error(url, result)

    md = result.markdown
    content = (md.fit_markdown or md.raw_markdown) if md else ""
    return content


@mcp.tool(
    title="Create a browser session",
    annotations=ToolAnnotations(
        read_only_hint=False,  # allocates a persistent browser page and cookie jar
        destructive_hint=False,  # additive; refuses rather than replacing an existing session
        idempotent_hint=False,  # session_id=None mints a fresh UUID on every call
        open_world_hint=True,  # optionally navigates to a caller-supplied URL
    ),
)
async def create_session(
    session_id: str | None = None,
    url: str | None = None,
    cookies: list | None = None,
    headers: dict | None = None,
    ctx: Context[AppContext] = None,
) -> str:
    """Create a named browser session for multi-step authenticated workflows.

    The session maintains cookies, localStorage, and browser state across
    multiple crawl_url calls that reference the same session_id.

    Sessions have a 30-minute inactivity TTL — each crawl_url call with the
    session_id resets the timer.

    A session is NOT a security boundary. crawl4ai 0.9.2 gives sessions no
    private cookie storage: every session shares the browser context's one
    cookie jar, so a credential held by this session is also sent on other
    crawls of the same domain, including crawls that name a different session
    or no session at all. It stays domain-scoped, so it does not reach other
    hosts, and destroy_session clears it immediately. To keep two identities
    on one site genuinely apart, use separate server processes. See the
    `cookies` note on crawl_url for the full detail.

    Args:
        session_id: Name for the session. If not provided, a UUID is generated.
            Use a descriptive name like "github-auth" or "dashboard-session".

        url: Optional URL to navigate to during session creation. Useful for
            login pages where you want to combine session creation with an
            initial crawl. If omitted, the session page is created without
            navigating anywhere.

        cookies: Optional list of cookie dicts to inject into the session.
            Each cookie must have at minimum: name, value, domain.
            These cookies persist in the session for subsequent crawl_url calls.

        headers: Optional dict of HTTP headers to send with the initial request.
            Only applied if url is also provided.
    """
    app: AppContext = ctx.request_context.lifespan_context
    sid = session_id or str(uuid.uuid4())

    if sid in app.sessions:
        return f"Session already exists: {sid}"

    logger.info("create_session: %s (url=%s)", sid, url)

    if url:
        # Create session by crawling a URL (e.g., a login page)
        config = build_run_config(
            app.profile_manager,
            None,
            session_id=sid,
            cache_mode=CacheMode.BYPASS,
        )
        result = await _crawl_with_overrides(
            _require_crawler(app), url, config, headers, cookies
        )

        app.sessions[sid] = time.time()

        if not result.success:
            return f"Session created: {sid}\n\nWarning: initial crawl failed:\n{_format_crawl_error(url, result)}"

        md = result.markdown
        content = (md.fit_markdown or md.raw_markdown) if md else ""
        return f"Session created: {sid}\n\nInitial page content:\n{content}"
    else:
        # Cookies cannot be injected without a real page to inject them into.
        #
        # This used to crawl "about:blank" to fire the cookie hook. crawl4ai
        # rejects that URL outright ("URL must start with 'http://', 'https://',
        # file:// or raw:"), the hook never ran, and the failed result was
        # discarded without checking success -- so the tool reported "Session
        # created" while silently dropping every cookie it was handed. The
        # caller then made authenticated calls that carried no credential and
        # got an unexplained login page back.
        #
        # Refusing is the honest answer: the cookies genuinely cannot be
        # applied here, and saying so points at the one-line fix.
        app.sessions[sid] = time.time()
        if cookies:
            return (
                f"Session created: {sid}\n\n"
                f"WARNING: the {len(cookies)} cookie(s) you passed were NOT applied. "
                f"Cookies can only be injected during a real page load, so pass "
                f"`url` to create_session (any page on the target domain will do), "
                f"or pass the cookies to your first crawl_url call with this "
                f"session_id."
            )
        return f"Session created: {sid}"


@mcp.tool(
    title="List browser sessions",
    annotations=ToolAnnotations(
        read_only_hint=True,
        open_world_hint=False,  # reads the in-memory session table
    ),
)
async def list_sessions(
    ctx: Context[AppContext] = None,
) -> str:
    """List all active named browser sessions.

    Shows each session's name and how long ago it was created.
    Sessions have a 30-minute inactivity TTL managed by crawl4ai —
    a session may have been auto-expired by crawl4ai even if it
    still appears here. The next crawl_url call with an expired
    session_id will transparently create a fresh session.
    """
    app: AppContext = ctx.request_context.lifespan_context
    if not app.sessions:
        return "No active sessions."

    # Prefer crawl4ai's own registry: it stores (context, page, last_used) and
    # refreshes last_used on every crawl. Our dict only records creation time,
    # so a session used a minute ago but created ninety minutes ago rendered as
    # "created 90 min ago" right next to a documented 30-minute TTL, implying it
    # was dead when it was live. The TTL is measured from last use, not creation.
    native: dict = {}
    ttl = 1800.0
    try:
        manager = app.crawler.crawler_strategy.browser_manager
        native = getattr(manager, "sessions", {}) or {}
        ttl = float(getattr(manager, "session_ttl", 1800))
    except AttributeError:  # pragma: no cover - upstream layout change
        pass

    lines = ["Active sessions:"]
    now = time.time()
    for sid, created in sorted(app.sessions.items()):
        entry = native.get(sid)
        if entry is not None:
            last_used = entry[2]
            idle = now - last_used
            state = "expired" if idle > ttl else "live"
            lines.append(
                f"  - {sid} (last used {idle / 60:.0f} min ago, {state}; "
                f"expires after {ttl / 60:.0f} min idle)"
            )
        else:
            # Named but never materialised: create_session with no url and no
            # cookies registers the name here without opening a browser page.
            lines.append(
                f"  - {sid} (declared {(now - created) / 60:.0f} min ago, "
                "no browser page opened yet)"
            )
    return "\n".join(lines)


@mcp.tool(
    title="Destroy a browser session",
    annotations=ToolAnnotations(
        read_only_hint=False,
        # The only tool here that tears something down: it kills the browser
        # page and discards its cookies and localStorage. Not recoverable.
        destructive_hint=True,
        idempotent_hint=True,  # a second call reports "not found" and changes nothing
        open_world_hint=False,  # acts on server-side state only
    ),
)
async def destroy_session(
    session_id: str,
    ctx: Context[AppContext] = None,
) -> str:
    """Destroy a named browser session and free its resources.

    Closes the session's browser page and context. The session_id
    can no longer be used with crawl_url after destruction.

    Args:
        session_id: The session name to destroy. Use list_sessions
            to see available sessions.
    """
    app: AppContext = ctx.request_context.lifespan_context
    if session_id not in app.sessions:
        return f"Session not found: {session_id}"

    logger.info("destroy_session: %s", session_id)
    try:
        await _require_crawler(app).crawler_strategy.kill_session(session_id)
    except Exception as exc:
        logger.warning("Error killing session %s: %s", session_id, exc)
    del app.sessions[session_id]
    return f"Session destroyed: {session_id}"


@mcp.tool(
    title="Crawl many URLs concurrently",
    annotations=ToolAnnotations(
        read_only_hint=False,  # js_code runs caller JS in-page; output_dir writes files
        destructive_hint=False,  # additive: cache entries and new files under output_dir
        idempotent_hint=False,  # js_code may have side effects on each call
        open_world_hint=True,  # fetches caller-supplied URLs
    ),
)
async def crawl_many(
    urls: list[str],
    max_concurrent: int = 10,
    delay: float = 0,
    output_dir: str | None = None,
    include_links: bool = False,
    include_tables: bool = False,
    profile: str | None = None,
    query: str | None = None,
    cache_mode: str | None = None,
    css_selector: str | None = None,
    target_elements: list[str] | None = None,
    excluded_selector: str | None = None,
    wait_for: str | None = None,
    js_code: str | None = None,
    user_agent: str | None = None,
    page_timeout: int | None = None,
    word_count_threshold: int | None = None,
    ctx: Context[AppContext] = None,
) -> CrawlBatchResult:
    """Crawl multiple URLs concurrently and return all results.

    URLs are crawled in parallel (not sequentially) using a semaphore-based
    dispatcher. The `max_concurrent` parameter controls how many URLs are
    crawled simultaneously (default 10). Per-call parameters apply to ALL
    URLs in the batch.

    Individual URL failures never fail the entire batch — the result always
    includes both successes and failures so you can reason about partial results.

    Note: per-call headers and cookies are not supported for batch crawls.
    Use crawl_url for requests requiring custom headers or cookies.

    Args:
        urls: List of URLs to crawl concurrently.

        max_concurrent: Maximum number of URLs to crawl simultaneously
            (default 10). Higher values are faster but use more memory.
            There is no artificial cap — set as high as needed.

        delay: Politeness delay in seconds between requests (default 0 — no
            delay). When > 0, a RateLimiter paces requests to avoid
            overwhelming target servers.

        output_dir: Directory to write per-page .md files and a manifest.json.
            When set, returns a metadata summary (file paths) instead of page
            content. When None (default), returns full content inline.
            Existing files are overwritten without warning when their names
            collide — manifest.json always is. Point this at a directory you
            own, not one holding files you need.

        include_links: Also return each page's outgoing links, split into
            internal and external (default False). crawl4ai collects these on
            every crawl regardless; they are off by default because they are
            frequently larger than the page content itself. Measured on one
            Wikipedia article: 997 internal links, about 130KB of JSON. Turn
            this on to map a site's structure, or to choose what to crawl next
            without paying for a full deep crawl. Requested links still come
            back inline when output_dir is set; only markdown goes to disk.

        include_tables: Also return tabular data crawl4ai extracted from each
            page, as headers plus rows plus caption (default False). crawl4ai
            scores every table and keeps only those it judges to be real data,
            so page-layout tables are already filtered out. Off by default for
            the same size reason — a single reference table can run to hundreds
            of rows — but nothing is truncated when it is on.

        profile: Name of a crawl profile to use as base configuration.
            Per-call parameters take precedence over profile values.
            Use list_profiles to see available profiles.

        query: Filter the page to the parts relevant to this question, before
            any tokens are spent. Swaps the default density-based pruning for
            crawl4ai's BM25 scoring, which ranks each block against the query.
            Measured on a docs page: 9,278 characters became 2,165, and the
            retained text was the sections that answered the question. No LLM
            and no cost. Leave unset to keep the density filter.

        cache_mode: Controls crawl4ai's cache read/write behaviour.
            - "enabled"    — use cache if available, fetch and store on miss (default)
            - "bypass"     — always fetch fresh; do not read or write cache
            - "disabled"   — fetch fresh; no cache read or write for this session
            - "read_only"  — return cached result only; fail if not cached
            - "write_only" — fetch fresh and overwrite cache; ignore existing cached

        css_selector: Restrict extraction to elements matching this CSS selector
            (include scope). Applied to ALL URLs in the batch.

            Narrows the DOCUMENT, not just the output: page title and meta
            description come back None, and links are limited to the scope.
            Prefer target_elements to keep them.

        target_elements: Restrict the MARKDOWN to these CSS selectors (a list)
            while leaving title, description, and links intact. Same markdown as
            css_selector, without discarding the rest of the page. Applied to
            ALL URLs in the batch.

        excluded_selector: Exclude elements matching this CSS selector from
            extraction. Applied to ALL URLs in the batch.

        wait_for: Wait until a CSS selector or JavaScript condition is met before
            extracting content. Applied to ALL URLs in the batch.

        js_code: JavaScript to execute in each page after load and before
            extraction. Applied to ALL URLs in the batch.

        user_agent: Override the browser User-Agent string. Not reliably
            per-call: crawl4ai applies it by mutating the shared browser config
            and browser contexts are cached, so the FIRST agent used for a
            context wins for that context's lifetime. Later calls passing a
            different one are ignored, and calls passing none inherit it.

        page_timeout: Maximum seconds to wait for each page to load (default 60).

        word_count_threshold: Minimum word count for a content block to survive
            PruningContentFilter (default 10).
    """
    resolved_cache, cache_error = _resolve_cache_mode(cache_mode)
    if cache_error:
        return CrawlBatchResult(crawled=0, total=0, pages=[], error=cache_error)

    logger.info(
        "crawl_many: %d URLs (max_concurrent=%d, delay=%.1f, profile=%s)",
        len(urls),
        max_concurrent,
        delay,
        profile,
    )

    # Build per-call kwargs — only include optional params when explicitly set
    per_call_kwargs: dict = {"cache_mode": resolved_cache}
    if page_timeout is not None:
        per_call_kwargs["page_timeout"] = page_timeout * 1000
    if css_selector is not None:
        per_call_kwargs["css_selector"] = css_selector
    if target_elements is not None:
        per_call_kwargs["target_elements"] = target_elements
    if excluded_selector is not None:
        per_call_kwargs["excluded_selector"] = excluded_selector
    if wait_for is not None:
        per_call_kwargs["wait_for"] = wait_for
    if js_code is not None:
        per_call_kwargs["js_code"] = js_code
    if user_agent is not None:
        per_call_kwargs["user_agent"] = user_agent
    if word_count_threshold is not None:
        per_call_kwargs["word_count_threshold"] = word_count_threshold
    if query is not None:
        per_call_kwargs["query"] = query

    app: AppContext = ctx.request_context.lifespan_context
    run_cfg = build_run_config(app.profile_manager, profile, **per_call_kwargs)

    rate_limiter = RateLimiter(base_delay=(delay, delay)) if delay > 0 else None
    dispatcher = SemaphoreDispatcher(
        semaphore_count=max_concurrent,
        rate_limiter=rate_limiter,
        # NO monitor — CrawlerMonitor uses Rich Console -> stdout corruption
    )

    # Heartbeat while the batch runs, so a long crawl is not aborted for
    # idleness. Per-page progress would need a streaming dispatcher, and the
    # only one crawl4ai ships that streams is MemoryAdaptiveDispatcher, which
    # stalls dispatch above a system-memory threshold; that is not a failure
    # mode worth adding to every user's crawls for a nicer progress message.
    results = await _await_with_heartbeat(
        _require_crawler(app).arun_many(
            urls=urls,
            config=run_cfg,
            dispatcher=dispatcher,
        ),
        ctx,
        f"Crawling {len(urls)} URLs",
    )

    if output_dir:
        return _persist_results(
            results,
            output_dir,
            include_links=include_links,
            include_tables=include_tables,
        )
    return _batch_result(
        results, include_links=include_links, include_tables=include_tables
    )


@mcp.tool(
    title="Extract structured JSON with an LLM (paid)",
    annotations=ToolAnnotations(
        read_only_hint=False,  # js_code runs caller JS in-page, and this call costs money
        destructive_hint=False,  # nothing is torn down
        idempotent_hint=False,  # every call bills the provider again
        open_world_hint=True,  # fetches a caller-supplied URL and calls an external LLM
    ),
)
async def extract_structured(
    url: str,
    schema: dict,
    instruction: str,
    provider: str = "openai/gpt-4o-mini",
    apply_chunking: bool = True,
    chunk_token_threshold: int | None = None,
    css_selector: str | None = None,
    wait_for: str | None = None,
    js_code: str | None = None,
    page_timeout: int | None = None,
    ctx: Context[AppContext] = None,
) -> str:
    """Extract structured JSON from a page using an LLM.

    WARNING: This tool calls an external LLM API and incurs token costs.
    Each call may cost $0.01-$1+ depending on page size and model.
    Use extract_css for cost-free deterministic extraction when possible.

    Args:
        url: The URL to crawl and extract data from.
        schema: JSON Schema dict describing the desired output structure.
            Accepts both Pydantic .model_json_schema() output and simple
            {"type": "object", "properties": {...}} format.
        instruction: Natural language instruction for the LLM describing
            what to extract from the page content.
        apply_chunking: Whether to split a long page into chunks (default True,
            matching crawl4ai). Each chunk is a separate LLM call and the
            results are concatenated, so a page over roughly 1500 words returns
            several schema-shaped objects instead of one, and costs more. Pass
            False for a single coherent result on a long page.

        chunk_token_threshold: Chunk size in tokens when chunking is on
            (crawl4ai's default is 2048). Raise it to keep more of the page in
            one call.

        provider: LLM provider and model in litellm format (default:
            "openai/gpt-4o-mini"). Examples: "anthropic/claude-sonnet-4-5",
            "gemini/gemini-2.5-flash". Model names go stale: providers retire
            them, and the call then fails with a NotFound from the provider
            rather than anything this server can detect up front. If that
            happens, check the provider's current model list.
            The API key is read from the
            corresponding environment variable (e.g. OPENAI_API_KEY) —
            never pass keys as parameters.
        css_selector: Restrict extraction scope to elements matching this
            CSS selector before passing content to the LLM.
        wait_for: Wait condition before extraction (CSS: "css:#el",
            JS: "js:() => expr").
        js_code: JavaScript to execute after page load, before extraction.
        page_timeout: Page load timeout in seconds (default 60).
    """
    # Pre-validate API key before attempting LLM call
    key_error = _check_api_key(provider)
    if key_error is not None:
        return key_error

    logger.info("extract_structured: %s (provider=%s)", url, provider)

    llm_config = LLMConfig(provider=provider)
    # Chunking is on by default in crawl4ai at 2048 tokens, and each chunk is a
    # SEPARATE LLM call whose results are concatenated. For a schema describing
    # one object, any page over roughly 1500 words therefore returns several
    # schema-shaped blobs rather than the single object the schema implies, and
    # bills for every chunk. Exposed here so a caller can turn it off for a
    # coherent single result, or raise the threshold, instead of being
    # surprised by it.
    strategy_kwargs: dict = {
        "llm_config": llm_config,
        "schema": schema,
        "extraction_type": "schema",
        "instruction": instruction,
        "input_format": "fit_markdown",
        "verbose": False,  # CRITICAL: protect MCP transport
        "apply_chunking": apply_chunking,
    }
    if chunk_token_threshold is not None:
        strategy_kwargs["chunk_token_threshold"] = chunk_token_threshold
    strategy = LLMExtractionStrategy(**strategy_kwargs)

    # Build CrawlerRunConfig directly (not via build_run_config) —
    # extraction tools don't need markdown_generator or profile merging.
    run_cfg = CrawlerRunConfig(
        extraction_strategy=strategy,
        page_timeout=(page_timeout or DEFAULT_PAGE_TIMEOUT_S) * 1000,
        verbose=False,  # CRITICAL: protect MCP transport
    )
    if css_selector is not None:
        run_cfg.css_selector = css_selector
    if wait_for is not None:
        run_cfg.wait_for = wait_for
    if js_code is not None:
        run_cfg.js_code = js_code

    app: AppContext = ctx.request_context.lifespan_context
    result = await _crawl_with_overrides(_require_crawler(app), url, run_cfg)

    if not result.success:
        return _format_crawl_error(url, result)

    # `"[]"` is a truthy string, so testing `not extracted_content` alone let an
    # empty extraction through as a success: a css_selector that matched nothing
    # returned the literal "[]" plus a token-usage footer, and the caller was
    # billed for a call that found nothing and told nothing. extract_css has
    # always tested both; this is the same test.
    if not result.extracted_content or result.extracted_content.strip() in (
        "[]",
        "{}",
    ):
        return (
            f"Extraction returned no data\n"
            f"URL: {url}\n"
            f"The LLM did not produce structured output. "
            f"Check that the schema matches the page content, and that any "
            f"css_selector you passed actually matches something on the page."
        )

    # crawl4ai does not raise when the LLM call fails; it puts the failure
    # INSIDE extracted_content as [{"index": 0, "error": true, "content": ...}]
    # and still reports result.success. Passed through verbatim that reads like
    # a normal extraction, so a retired model name or a bad key comes back
    # looking like data with a quiet "Total tokens: 0" underneath. Surface it.
    llm_error = _extraction_error(result.extracted_content)
    if llm_error:
        return (
            f"LLM extraction failed\n"
            f"URL: {url}\n"
            f"Provider: {provider}\n"
            f"Error: {llm_error}"
        )

    # Report token usage — NEVER call strategy.show_usage() (uses print())
    usage = strategy.total_usage
    return (
        f"{result.extracted_content}\n\n"
        f"--- LLM Usage ---\n"
        f"Provider: {provider}\n"
        f"Prompt tokens: {usage.prompt_tokens}\n"
        f"Completion tokens: {usage.completion_tokens}\n"
        f"Total tokens: {usage.total_tokens}"
    )


@mcp.tool(
    title="Extract structured JSON with CSS or XPath selectors (free)",
    annotations=ToolAnnotations(
        read_only_hint=False,  # js_code runs caller JS in-page
        destructive_hint=False,  # nothing is torn down
        idempotent_hint=False,  # js_code may have side effects on each call
        open_world_hint=True,  # fetches a caller-supplied URL
    ),
)
async def extract_css(
    url: str,
    schema: dict,
    selector_type: str = "css",
    css_selector: str | None = None,
    wait_for: str | None = None,
    js_code: str | None = None,
    page_timeout: int | None = None,
    ctx: Context[AppContext] = None,
) -> ExtractionResult:
    """Extract structured JSON from a page using CSS or XPath selectors (no LLM, no cost).

    Uses crawl4ai's JsonCssExtractionStrategy or JsonXPathExtractionStrategy for
    deterministic, repeatable extraction. No LLM API call is made — this tool is
    completely free to use.

    Args:
        url: The URL to crawl and extract data from.

        selector_type: Which selector language the schema is written in.
            - "css" (default): CSS selectors, e.g. "div.product h2".
            - "xpath": XPath 1.0 expressions, e.g. "//div[@class='product']/h2".

            Both take the identical schema shape below; only the selector
            strings change. Reach for XPath when CSS cannot express the match:
            selecting by text content (`//a[contains(text(), 'Download')]`),
            walking to a parent or preceding sibling, or indexing into a
            position. Prefer CSS otherwise — it is shorter and far more people
            can read it.

        schema: Extraction schema dict defining what to extract. Must contain:
            - "name": A label for the extraction (e.g. "Products")
            - "baseSelector": Selector matching each repeating item
              (e.g. "div.product-card", or "//div[@class='product-card']")
            - "fields": List of field definitions, each with:
              - "name": Field name in output JSON
              - "selector": Selector relative to baseSelector. With
                selector_type="xpath", write relative paths as "./" or ".//".
                A leading "//" does NOT escape to the whole document: crawl4ai
                evaluates field selectors context-sensitively and silently
                re-roots "//foo" to ".//foo". So "//title" inside an item
                matches nothing rather than the page title, and there is no way
                to reach outside the matched item from a field selector.
              - "type": One of "text", "attribute", "html", "regex",
                "list", "nested", "nested_list". An unrecognised value is NOT
                an error upstream: crawl4ai falls through and returns the
                element's raw HTML as the field value, so a typo like "textt"
                yields markup rather than text.
              - "pattern": REQUIRED when type is "regex". Without it the field
                returns the element's raw HTML.
              - "group": Optional capture group for type "regex". Defaults to
                1, not 0, so a pattern with no capture group needs "group": 0.
              - "attribute": Required when type is "attribute" (e.g. "href", "src")
              - "transform": Optional, e.g. "strip", "lowercase"
              - "default": Optional default value if selector matches nothing
              - "fields": Required for "nested"/"nested_list"/"list" types
                (recursive field definitions)

            CSS example:
            {
                "name": "Products",
                "baseSelector": "div.product",
                "fields": [
                    {"name": "title", "selector": "h2", "type": "text"},
                    {"name": "price", "selector": ".price", "type": "text"},
                    {"name": "url", "selector": "a", "type": "attribute",
                     "attribute": "href"}
                ]
            }

            The same schema in XPath:
            {
                "name": "Products",
                "baseSelector": "//div[@class='product']",
                "fields": [
                    {"name": "title", "selector": ".//h2", "type": "text"},
                    {"name": "price", "selector": ".//*[@class='price']", "type": "text"},
                    {"name": "url", "selector": ".//a", "type": "attribute",
                     "attribute": "href"}
                ]
            }

        css_selector: Restrict extraction scope to elements matching this
            CSS selector before applying the extraction schema. Always CSS,
            regardless of selector_type: it is crawl4ai's page-level scoping
            parameter, applied before the schema runs.

        wait_for: Wait condition before extraction (CSS: "css:#el",
            JS: "js:() => expr"). Useful for dynamically loaded content.

        js_code: JavaScript to execute after page load, before extraction.
            Use to trigger lazy loading or expand collapsed sections.

        page_timeout: Page load timeout in seconds (default 60).
    """
    logger.info("extract_css: %s (selector_type=%s)", url, selector_type)

    # An unrecognised selector_type is refused rather than defaulted, unlike
    # cache_mode elsewhere in this server. Falling back to CSS would feed XPath
    # expressions to a CSS parser, and the caller would get "your selectors
    # matched nothing" — a message pointing at the schema when the real fault
    # is one misspelled argument.
    strategies = {
        "css": JsonCssExtractionStrategy,
        "xpath": JsonXPathExtractionStrategy,
    }
    strategy_cls = strategies.get(selector_type.lower().strip())
    if strategy_cls is None:
        return ExtractionResult(
            url=url,
            count=0,
            items=[],
            error=(
                f"Unknown selector_type {selector_type!r}. "
                f"Use 'css' (default) or 'xpath'."
            ),
        )

    strategy = strategy_cls(schema, verbose=False)

    # Build CrawlerRunConfig directly (not via build_run_config) —
    # extraction tools don't need markdown_generator or profile merging.
    run_cfg = CrawlerRunConfig(
        extraction_strategy=strategy,
        page_timeout=(page_timeout or DEFAULT_PAGE_TIMEOUT_S) * 1000,
        verbose=False,  # CRITICAL: protect MCP transport
    )
    if css_selector is not None:
        run_cfg.css_selector = css_selector
    if wait_for is not None:
        run_cfg.wait_for = wait_for
    if js_code is not None:
        run_cfg.js_code = js_code

    app: AppContext = ctx.request_context.lifespan_context
    result = await _crawl_with_overrides(_require_crawler(app), url, run_cfg)

    if not result.success:
        return ExtractionResult(
            url=url, count=0, items=[], error=_format_crawl_error(url, result)
        )

    if not result.extracted_content or result.extracted_content == "[]":
        return ExtractionResult(
            url=url,
            count=0,
            items=[],
            error=(
                f"The {selector_type.lower().strip()} selectors in the schema did not "
                "match any elements on the page. Verify that baseSelector and the "
                "field selectors are correct for this page's HTML structure."
            ),
        )

    # crawl4ai hands back a JSON string. Parse it so the caller gets real data
    # rather than JSON embedded in JSON. A parse failure is reportable, not fatal.
    try:
        items = json.loads(result.extracted_content)
    except json.JSONDecodeError as exc:
        return ExtractionResult(
            url=url,
            count=0,
            items=[],
            error=f"Extraction returned malformed JSON: {exc}",
        )

    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        return ExtractionResult(
            url=url,
            count=0,
            items=[],
            error=f"Extraction returned {type(items).__name__}, expected a list of records.",
        )

    return ExtractionResult(
        url=url,
        count=len(items),
        items=[i for i in items if isinstance(i, dict)],
    )


@mcp.tool(
    title="Extract emails, phones, prices and more (free)",
    annotations=ToolAnnotations(
        read_only_hint=False,  # js_code runs caller JS in-page
        destructive_hint=False,  # nothing is torn down
        idempotent_hint=False,  # js_code may have side effects on each call
        open_world_hint=True,  # fetches a caller-supplied URL
    ),
)
async def extract_patterns(
    url: str,
    patterns: list[str] | None = None,
    custom_patterns: dict | None = None,
    css_selector: str | None = None,
    wait_for: str | None = None,
    js_code: str | None = None,
    page_timeout: int | None = None,
    ctx: Context[AppContext] = None,
) -> ExtractionResult:
    """Pull common data types off a page with regex. No LLM, no cost, no schema.

    Use this instead of extract_structured when you want well-known shapes --
    emails, phone numbers, prices, dates, URLs -- rather than page-specific
    fields. It needs no schema authoring and costs nothing, so it is the right
    first reach for "get me all the contact details on this page".

    Use extract_css when you need page-specific fields tied to markup, and
    extract_structured when the data needs actual understanding.

    Args:
        url: The URL to extract from.

        patterns: Which built-in patterns to look for. Any of:
            email, phone_intl, phone_us, url, ipv4, ipv6, uuid, currency,
            percentage, number, date_iso, date_us, time_24h, postal_us,
            postal_uk, html_color_hex, twitter_handle, hashtag, mac_addr,
            iban, credit_card.
            Defaults to ["email", "phone_us", "url"] -- passing every pattern
            at once returns a lot of noise, so ask for what you want.

        custom_patterns: Your own named regexes, as {"name": "pattern"}. Merged
            with any built-ins requested. Example: {"sku": "SKU-[0-9]{6}"}.

        css_selector: Restrict extraction to elements matching this selector.
        wait_for: Wait condition before extracting (CSS: "css:#el", JS: "js:() => expr").
        js_code: JavaScript to run after load, before extraction.
        page_timeout: Page load timeout in seconds (default 60).
    """
    selected = patterns if patterns is not None else ["email", "phone_us", "url"]

    flag = RegexExtractionStrategy._B.NOTHING
    unknown: list[str] = []
    for name in selected:
        member = getattr(RegexExtractionStrategy._B, name.upper(), None)
        if member is None:
            unknown.append(name)
            continue
        flag |= member
    if unknown:
        return ExtractionResult(
            url=url,
            count=0,
            items=[],
            error=(
                f"Unknown pattern name(s): {', '.join(unknown)}. Valid names are: "
                + ", ".join(
                    m.name.lower()
                    for m in RegexExtractionStrategy._B
                    if m.name not in {"NOTHING", "ALL"}
                )
            ),
        )
    if flag == RegexExtractionStrategy._B.NOTHING and not custom_patterns:
        return ExtractionResult(
            url=url,
            count=0,
            items=[],
            error="No patterns requested. Pass `patterns`, `custom_patterns`, or both.",
        )

    # Validate the caller's regexes here rather than letting re.compile raise
    # from inside crawl4ai. An invalid pattern is caller input, not a server
    # fault, and it used to crash the whole tool call: the caller got
    # "Error executing tool extract_patterns" with no structuredContent and no
    # indication of WHICH pattern was bad. A typo in one of five regexes should
    # name that regex, not take the request down.
    if custom_patterns:
        for name, pattern in custom_patterns.items():
            if not isinstance(pattern, str):
                return ExtractionResult(
                    url=url,
                    count=0,
                    items=[],
                    error=(
                        f"custom_patterns[{name!r}] must be a regex string, got "
                        f"{type(pattern).__name__}."
                    ),
                )
            try:
                re.compile(pattern)
            except re.error as exc:
                return ExtractionResult(
                    url=url,
                    count=0,
                    items=[],
                    error=(
                        f"custom_patterns[{name!r}] is not a valid regular "
                        f"expression: {exc}"
                    ),
                )

    logger.info("extract_patterns: %s (patterns=%s)", url, selected)

    # input_format="html", not crawl4ai's default of "fit_html". fit_html is the
    # content-filtered HTML, and this tool builds a bare config with no filter,
    # so it is nearly empty: measured on python.org/about/help, fit_html yielded
    # 3 emails and ZERO urls while html yielded 6 emails and 77 urls. The
    # default would silently under-report rather than fail.
    strategy = RegexExtractionStrategy(
        flag, custom=custom_patterns or None, input_format="html"
    )
    run_cfg = CrawlerRunConfig(
        extraction_strategy=strategy,
        page_timeout=(page_timeout or DEFAULT_PAGE_TIMEOUT_S) * 1000,
        verbose=False,  # CRITICAL: protect MCP transport
    )
    if css_selector is not None:
        run_cfg.css_selector = css_selector
    if wait_for is not None:
        run_cfg.wait_for = wait_for
    if js_code is not None:
        run_cfg.js_code = js_code

    app: AppContext = ctx.request_context.lifespan_context
    result = await _crawl_with_overrides(_require_crawler(app), url, run_cfg)

    if not result.success:
        return ExtractionResult(
            url=url, count=0, items=[], error=_format_crawl_error(url, result)
        )

    if not result.extracted_content or result.extracted_content == "[]":
        return ExtractionResult(
            url=url,
            count=0,
            items=[],
            error=(
                "No matches on this page for the requested patterns. The page may "
                "genuinely contain none, or the data may be rendered by JavaScript "
                "that had not run yet -- try wait_for or the js_heavy profile."
            ),
        )

    try:
        items = json.loads(result.extracted_content)
    except json.JSONDecodeError as exc:
        return ExtractionResult(
            url=url,
            count=0,
            items=[],
            error=f"Extraction returned malformed JSON: {exc}",
        )

    if isinstance(items, dict):
        items = [items]
    items = [i for i in items if isinstance(i, dict)]
    return ExtractionResult(url=url, count=len(items), items=items)


@mcp.tool(
    title="Crawl a site by following links",
    annotations=ToolAnnotations(
        read_only_hint=False,  # js_code runs caller JS in-page; output_dir writes files
        destructive_hint=False,  # additive: cache entries and new files under output_dir
        idempotent_hint=False,  # js_code may have side effects on each call
        open_world_hint=True,  # follows links discovered at crawl time
    ),
)
async def deep_crawl(
    url: str,
    max_depth: int = 3,
    max_pages: int = 100,
    scope: str = "same-domain",
    strategy: str = "bfs",
    relevance_keywords: list[str] | None = None,
    include_pattern: str | None = None,
    exclude_pattern: str | None = None,
    allowed_domains: list[str] | None = None,
    blocked_domains: list[str] | None = None,
    max_concurrent: int = 5,
    delay: float = 0,
    output_dir: str | None = None,
    include_links: bool = False,
    include_tables: bool = False,
    profile: str | None = None,
    query: str | None = None,
    cache_mode: str | None = None,
    css_selector: str | None = None,
    target_elements: list[str] | None = None,
    excluded_selector: str | None = None,
    wait_for: str | None = None,
    js_code: str | None = None,
    user_agent: str | None = None,
    page_timeout: int | None = None,
    word_count_threshold: int | None = None,
    ctx: Context[AppContext] = None,
) -> CrawlBatchResult:
    """Crawl a site by following links from a start URL using BFS (breadth-first search).

    Starting from the given URL, discovers all links on the page, crawls them,
    discovers their links, and repeats up to max_depth levels deep. Stops when
    max_pages total pages have been crawled or no more links are found.

    Each URL is crawled at most once (automatic deduplication). Results include
    depth (how many links away from the start URL) and parent_url metadata.

    Args:
        url: The starting URL to begin the crawl from.

        max_depth: Maximum number of link levels to follow from the start URL
            (default 3). Depth 0 is the start page, depth 1 is pages linked
            from the start page, etc.

        max_pages: Hard cap on total pages crawled (default 100). The crawl
            stops when this many pages have been successfully crawled, even if
            more links exist. Large values take proportionally longer — the
            agent controls this.

        scope: Domain scope for link following.
            - "same-domain" (default): Only follow links within the start URL's
              domain, INCLUDING its subdomains.
            - "same-origin": An alias for same-domain, not a stricter setting.
              crawl4ai has no origin-level scoping, so scheme and port are not
              compared and subdomains are still followed. If you need true
              same-origin, filter the results yourself.
            - "any": Follow all links including external domains.

        include_pattern: Glob pattern to filter which URLs to follow (e.g.,
            "/docs/*" to only follow documentation links). Only URLs matching
            this pattern will be crawled.

        exclude_pattern: Glob pattern to exclude URLs from following (e.g.,
            "/internal/*" to skip internal links). URLs matching this pattern
            will not be crawled.

        allowed_domains: Follow links only on these hosts. This is what `scope`
            cannot express: "the docs site AND its separate api host". Setting
            it REPLACES `scope` as the boundary of the crawl, because crawl4ai
            discards off-domain links before any filter runs, so an allowlist
            can only take effect once external links are let through.

            List every host you want crawled, INCLUDING the start URL's own.
            Leaving it out is legal and does something surprising: the start
            page is still fetched (depth 0 is not filtered), but every link
            back into its own site is dropped and only the listed hosts are
            traversed. The result carries a note when that happens rather than
            leaving you to work out why the crawl went sideways.

            Subdomains of a listed domain are included ("example.com" allows
            "api.example.com"), matching crawl4ai's rule everywhere else. Give
            the bare host with no scheme and no port.

        blocked_domains: Never follow links on these hosts, and their
            subdomains. Applies on top of whatever `scope` or `allowed_domains`
            already permits, so it can carve a host out of a same-domain crawl
            without widening anything.

        max_concurrent: Maximum pages fetched simultaneously (default 5,
            crawl4ai's own default for a deep crawl). Unlike crawl_many, a
            deep crawl cannot use a custom dispatcher — crawl4ai's deep-crawl
            strategy takes none — so this sets semaphore_count on the run
            config, which is what its internal dispatcher reads.

        delay: Politeness delay in seconds between page fetches (default 0 —
            no delay). Sets mean_delay, the inter-request pacing crawl4ai's
            internal dispatcher applies at every BFS level.

            Note: a deep crawl is dispatched by crawl4ai's MemoryAdaptiveDispatcher,
            which crawl_many and crawl_sitemap deliberately avoid because it
            pauses dispatch above a system-memory threshold. There is no way to
            substitute a dispatcher here, so a deep crawl on a memory-pressured
            machine can stall where a flat batch crawl would not.

        output_dir: Directory to write per-page .md files and a manifest.json.
            When set, returns a metadata summary (file paths) instead of page
            content. When None (default), returns full content inline.
            Existing files are overwritten without warning when their names
            collide — manifest.json always is. Point this at a directory you
            own, not one holding files you need.

        include_links: Also return each page's outgoing links, split into
            internal and external (default False). Off by default because the
            links frequently outweigh the page content: measured at 997
            internal links, about 130KB of JSON, for one Wikipedia article.
            Across a hundred-page deep crawl that multiplies.

        include_tables: Also return tabular data crawl4ai extracted from each
            page, as headers plus rows plus caption (default False). Only
            tables crawl4ai scored as real data are included, so page-layout
            tables are already filtered out. Nothing is truncated when on.

        query: Filter each page to the parts relevant to this question,
            using BM25 scoring instead of the default density filter. Free.
        profile: Named crawl profile for per-page configuration.
        cache_mode: Cache behavior (same as crawl_url).
        css_selector: Restrict extraction to matching elements on each page.
            Narrows the DOCUMENT: title, description and out-of-scope links are
            lost with it. Prefer target_elements to keep them.
        target_elements: Restrict the MARKDOWN to these CSS selectors (a list),
            leaving title, description and links intact.
        excluded_selector: Exclude matching elements from extraction.
        wait_for: Wait condition before extracting each page.
        js_code: JavaScript to execute on each page before extraction.
        user_agent: Override the browser User-Agent string. Not reliably
            per-call: crawl4ai applies it by mutating the shared browser config
            and browser contexts are cached, so the FIRST agent used for a
            context wins for that context's lifetime. Later calls passing a
            different one are ignored, and calls passing none inherit it.
        page_timeout: Page load timeout in seconds (default 60).
        word_count_threshold: Minimum word count for content blocks (default 10).

    Note:
        Per-request headers and cookies are not supported for deep_crawl in v1.
        Use crawl_url for single pages that need custom headers or cookies.
    """
    resolved_cache, cache_error = _resolve_cache_mode(cache_mode)
    if cache_error:
        return CrawlBatchResult(crawled=0, total=0, pages=[], error=cache_error)

    logger.info(
        "deep_crawl: %s (depth=%d, max_pages=%d, scope=%s, delay=%.1f)",
        url,
        max_depth,
        max_pages,
        scope,
        delay,
    )

    # Build filter chain from agent params
    filters = []
    if include_pattern is not None:
        filters.append(URLPatternFilter(patterns=[include_pattern]))
    if exclude_pattern is not None:
        filters.append(URLPatternFilter(patterns=[exclude_pattern], reverse=True))

    domain_filter = None
    if allowed_domains or blocked_domains:
        domain_filter = DomainFilter(
            allowed_domains=list(allowed_domains) if allowed_domains else None,
            blocked_domains=list(blocked_domains) if blocked_domains else None,
        )
        filters.append(domain_filter)

    filter_chain = FilterChain(filters=filters) if filters else FilterChain()

    # Map scope to include_external.
    #
    # "same-origin" is accepted as an alias, NOT as a stricter setting. crawl4ai
    # has no origin concept anywhere: its internal/external split compares
    # registrable domains, so subdomains count as internal and scheme and port
    # are ignored. Treating the two labels as different would be a promise the
    # library cannot keep, so the alias is honoured and called out instead.
    if scope in ("same-domain", "same-origin"):
        include_external = False
    elif scope == "any":
        include_external = True
    else:
        # Refused, not defaulted. Silently treating an unrecognised scope as
        # same-domain is the wrong way round: someone who typed "all" meaning
        # "any" would get a quietly narrower crawl and no indication of it.
        return CrawlBatchResult(
            crawled=0,
            total=0,
            pages=[],
            error=_bad_choice("scope", scope, ["same-domain", "same-origin", "any"]),
        )

    # An allowlist has to widen the link pool before it can narrow it.
    #
    # crawl4ai applies include_external in link_discovery, which chooses which
    # links even enter the candidate set, and runs the filter chain afterwards
    # on what survived (verified by reading BFSDeepCrawlStrategy.link_discovery
    # and can_process_url in 0.9.2). So with include_external False, an
    # off-domain host named in allowed_domains is already gone by the time the
    # DomainFilter could admit it, and the parameter would be silently inert —
    # the exact failure this capability exists to remove.
    #
    # Turning it on hands the boundary to the allowlist, which is stricter than
    # scope ever was: nothing outside the listed hosts is followed. Only
    # allowed_domains does this. blocked_domains subtracts, so it composes with
    # any scope without touching it.
    if allowed_domains:
        include_external = True

    # An allowlist that does not cover the start URL's own host is legal and
    # crawl4ai runs it without complaint, but it almost never means what it
    # looks like. Depth 0 bypasses the filter chain, so the start page is
    # fetched; every link back into its own site is then rejected, and only the
    # listed hosts are traversed. The caller asked to crawl a site and got the
    # site's front page plus somebody else's, with nothing saying why.
    #
    # Measured, not assumed: starting at docs.astral.sh/uv/ with
    # allowed_domains=["github.com"] crawled 4 pages, one of them the start
    # page and three on github.com. An earlier version of this note claimed
    # "only that page was crawled" and was simply wrong.
    #
    # Asks DomainFilter itself rather than reimplementing its matching, so the
    # note cannot drift from the rule actually being enforced. A throwaway
    # instance keeps this probe out of the live filter's own hit counters.
    scope_note = None
    if allowed_domains and not DomainFilter(
        allowed_domains=list(allowed_domains),
        blocked_domains=list(blocked_domains) if blocked_domains else None,
    ).apply(url):
        scope_note = (
            f"allowed_domains does not cover the start URL's own host, so links "
            f"from {url} back into that site were not followed. Only the listed "
            f"domains were crawled. Add the start URL's host to allowed_domains "
            f"to crawl the starting site as well."
        )
        logger.warning("deep_crawl: %s", scope_note)

    # MUST be fresh per call — the strategies carry mutable traversal state.
    #
    # best-first exists because max_pages truncates. Breadth-first spends the
    # budget on whatever happens to be shallow, so on a large site the cap is
    # reached before the pages the caller actually wanted. Scoring the frontier
    # by keyword relevance spends the same budget on the pages most likely to
    # matter, which is usually what an agent means by "crawl the docs for X".
    if strategy not in ("bfs", "best-first"):
        return CrawlBatchResult(
            crawled=0,
            total=0,
            pages=[],
            error=_bad_choice("strategy", strategy, ["bfs", "best-first"]),
        )

    if strategy == "best-first":
        scorer = (
            KeywordRelevanceScorer(keywords=relevance_keywords)
            if relevance_keywords
            else None
        )
        if scorer is None:
            logger.warning(
                "strategy='best-first' without relevance_keywords has nothing to "
                "rank by and degrades to an unordered crawl; pass keywords or use bfs"
            )
        crawl_strategy = BestFirstCrawlingStrategy(
            max_depth=max_depth,
            max_pages=max_pages,
            include_external=include_external,
            filter_chain=filter_chain,
            url_scorer=scorer,
        )
    else:
        # +1 compensates an upstream off-by-one, and the truncation below is
        # what makes that safe in both directions.
        #
        # BFSDeepCrawlStrategy._arun_stream increments its page counter, then
        # `break`s when it hits max_pages -- BEFORE the `yield`. So the last
        # page is fetched, counted, and thrown away. deep_crawl forces
        # stream=True so it can report progress (a silent tool call gets
        # aborted for idleness, which is why streaming is here at all), which
        # means every BFS deep crawl returned one page fewer than asked, and
        # max_pages=1 returned NOTHING.
        #
        # Measured, driving crawl4ai's own strategy directly:
        #     stream=False   1->1  3->3  5->5
        #     stream=True    1->0  3->2  5->4
        #
        # Asking for one extra and truncating our own results yields exactly
        # max_pages whether or not upstream ever fixes this: while the bug
        # exists we receive max_pages, and once it is fixed we receive
        # max_pages+1 and drop the extra.
        crawl_strategy = BFSDeepCrawlStrategy(
            max_depth=max_depth,
            max_pages=max_pages + 1,
            include_external=include_external,
            filter_chain=filter_chain,
        )

    # Build per-call kwargs — only include optional params when explicitly set
    per_call_kwargs: dict = {
        "cache_mode": resolved_cache,
        "deep_crawl_strategy": crawl_strategy,
    }
    if page_timeout is not None:
        per_call_kwargs["page_timeout"] = page_timeout * 1000
    # Politeness for a deep crawl is set on the run config, NOT via a
    # dispatcher. crawl4ai's DeepCrawlStrategy.arun() takes no dispatcher, and
    # BFS internally calls arun_many() without one, so crawl4ai builds its own
    # from mean_delay / max_range / semaphore_count on this config. Those three
    # fields are the only pacing controls that reach a deep crawl.
    #
    # This used to set delay_before_return_html instead, which is a per-page
    # "let JS settle" sleep that happens AFTER the page has already been
    # fetched. So delay=5 waited 5s per page while still firing requests at
    # crawl4ai's default ~0.1-0.4s cadence: the documented politeness delay hit
    # the target site far harder than the caller asked for.
    per_call_kwargs["semaphore_count"] = max_concurrent
    if delay > 0:
        per_call_kwargs["mean_delay"] = delay
        per_call_kwargs["max_range"] = 0.0
    if css_selector is not None:
        per_call_kwargs["css_selector"] = css_selector
    if target_elements is not None:
        per_call_kwargs["target_elements"] = target_elements
    if excluded_selector is not None:
        per_call_kwargs["excluded_selector"] = excluded_selector
    if wait_for is not None:
        per_call_kwargs["wait_for"] = wait_for
    if js_code is not None:
        per_call_kwargs["js_code"] = js_code
    if user_agent is not None:
        per_call_kwargs["user_agent"] = user_agent
    if word_count_threshold is not None:
        per_call_kwargs["word_count_threshold"] = word_count_threshold
    if query is not None:
        per_call_kwargs["query"] = query

    app: AppContext = ctx.request_context.lifespan_context
    run_cfg = build_run_config(app.profile_manager, profile, **per_call_kwargs)

    # With deep_crawl_strategy + stream, arun() returns an async generator that
    # yields each page as it is crawled, so progress can be reported. max_pages
    # is the cap rather than a known total, so it is the best "total" available.
    run_cfg.stream = True
    stream = await _require_crawler(app).arun(url=url, config=run_cfg)
    results = await _collect_with_progress(stream, ctx, max_pages, "Deep crawling")
    # Streaming yields in completion order; a stable sort by depth restores the
    # level-by-level grouping batch mode produced, without reordering within a level.
    results.sort(
        key=lambda r: (
            (r.metadata or {}).get("depth", 0) if isinstance(r.metadata, dict) else 0
        )
    )
    # Enforce the caller's cap ourselves. Sorting first means the pages kept are
    # the shallowest ones, which is what breadth-first is for. See the +1 on the
    # BFS strategy above: this is the half that stops it over-delivering if the
    # upstream off-by-one is ever fixed.
    if len(results) > max_pages:
        results = results[:max_pages]

    if output_dir:
        return _persist_results(
            results,
            output_dir,
            note=scope_note,
            include_links=include_links,
            include_tables=include_tables,
        )
    return _batch_result(
        results,
        note=scope_note,
        include_links=include_links,
        include_tables=include_tables,
    )


@mcp.tool(
    title="Crawl every URL in a sitemap",
    annotations=ToolAnnotations(
        read_only_hint=False,  # js_code runs caller JS in-page; output_dir writes files
        destructive_hint=False,  # additive: cache entries and new files under output_dir
        idempotent_hint=False,  # js_code may have side effects on each call
        open_world_hint=True,  # crawls whatever URLs the sitemap lists
    ),
)
async def crawl_sitemap(
    sitemap_url: str,
    max_urls: int = 500,
    max_concurrent: int = 10,
    delay: float = 0,
    output_dir: str | None = None,
    include_links: bool = False,
    include_tables: bool = False,
    profile: str | None = None,
    query: str | None = None,
    cache_mode: str | None = None,
    css_selector: str | None = None,
    target_elements: list[str] | None = None,
    excluded_selector: str | None = None,
    wait_for: str | None = None,
    js_code: str | None = None,
    user_agent: str | None = None,
    page_timeout: int | None = None,
    word_count_threshold: int | None = None,
    ctx: Context[AppContext] = None,
) -> CrawlBatchResult:
    """Crawl all pages listed in an XML sitemap.

    Fetches the sitemap XML via HTTP (not the browser -- sitemaps are plain XML),
    extracts all <loc> URLs, and crawls them concurrently via arun_many.

    Sitemap index files (<sitemapindex>) are automatically resolved by recursively
    fetching each referenced sub-sitemap. Gzipped sitemaps (.xml.gz) are
    automatically decompressed.

    Individual URL failures never fail the entire batch -- the result always
    includes both successes and failures so you can reason about partial results.

    Args:
        sitemap_url: URL of the XML sitemap (e.g. "https://example.com/sitemap.xml").

        max_urls: Maximum number of sitemap URLs to crawl (default 500). Large
            sitemaps can contain 50,000+ URLs -- this prevents runaway crawls.
            URLs beyond this limit are silently truncated with a note in the output.

        max_concurrent: Maximum number of URLs to crawl simultaneously
            (default 10). Higher values are faster but use more memory.

        delay: Politeness delay in seconds between requests (default 0 — no
            delay). When > 0, a RateLimiter paces requests to avoid
            overwhelming target servers.

        output_dir: Directory to write per-page .md files and a manifest.json.
            When set, returns a metadata summary (file paths) instead of page
            content. When None (default), returns full content inline.
            Existing files are overwritten without warning when their names
            collide — manifest.json always is. Point this at a directory you
            own, not one holding files you need.

        include_links: Also return each page's outgoing links, split into
            internal and external (default False). Off by default because the
            links frequently outweigh the page content: measured at 997
            internal links, about 130KB of JSON, for one Wikipedia article.
            A sitemap crawl covers hundreds of pages, so this multiplies.

        include_tables: Also return tabular data crawl4ai extracted from each
            page, as headers plus rows plus caption (default False). Only
            tables crawl4ai scored as real data are included, so page-layout
            tables are already filtered out. Nothing is truncated when on.

        query: Filter each page to the parts relevant to this question,
            using BM25 scoring instead of the default density filter. Free.
        profile: Named crawl profile for per-page configuration.
        cache_mode: Cache behavior (same as crawl_url).
        css_selector: Restrict extraction to matching elements on each page.
            Narrows the DOCUMENT: title, description and out-of-scope links are
            lost with it. Prefer target_elements to keep them.
        target_elements: Restrict the MARKDOWN to these CSS selectors (a list),
            leaving title, description and links intact.
        excluded_selector: Exclude matching elements from extraction.
        wait_for: Wait condition before extracting each page.
        js_code: JavaScript to execute on each page before extraction.
        user_agent: Override the browser User-Agent string. Not reliably
            per-call: crawl4ai applies it by mutating the shared browser config
            and browser contexts are cached, so the FIRST agent used for a
            context wins for that context's lifetime. Later calls passing a
            different one are ignored, and calls passing none inherit it.
        page_timeout: Page load timeout in seconds (default 60).
        word_count_threshold: Minimum word count for content blocks (default 10).

    Note:
        Per-call headers and cookies are not supported for sitemap crawls.
        Use crawl_url for requests requiring custom headers or cookies.
    """
    resolved_cache, cache_error = _resolve_cache_mode(cache_mode)
    if cache_error:
        return CrawlBatchResult(crawled=0, total=0, pages=[], error=cache_error)

    logger.info(
        "crawl_sitemap: %s (max_urls=%d, max_concurrent=%d, delay=%.1f)",
        sitemap_url,
        max_urls,
        max_concurrent,
        delay,
    )

    # Fetch and parse sitemap XML via httpx (not the browser).
    # These two paths must return the model, not a string: the tool declares
    # CrawlBatchResult, so returning a string here fails validation and the
    # caller gets an opaque tool crash instead of the reason the sitemap failed.
    try:
        urls = await _fetch_sitemap_urls(sitemap_url)
    except httpx.HTTPError as e:
        return CrawlBatchResult(
            crawled=0,
            total=0,
            pages=[],
            error=f"Could not fetch the sitemap at {sitemap_url}: {e}",
        )
    except ET.ParseError as e:
        # Distinct from a fetch failure on purpose. The usual cause is an HTML
        # page passed as the sitemap URL, and "fetch failed" would send someone
        # checking the network when the request actually succeeded.
        return CrawlBatchResult(
            crawled=0,
            total=0,
            pages=[],
            error=(
                f"{sitemap_url} was fetched but is not valid sitemap XML ({e}). "
                "If this is an HTML page, look for the real sitemap in "
                "robots.txt or try /sitemap.xml on the same host."
            ),
        )

    if not urls:
        return CrawlBatchResult(
            crawled=0,
            total=0,
            pages=[],
            error=(
                f"No URLs found in sitemap {sitemap_url}. It may be empty, or "
                "in a format this server does not parse. Sitemap indexes and "
                ".xml.gz are supported; an HTML page is not a sitemap."
            ),
        )

    # Truncate if over max_urls
    total_sitemap_urls = len(urls)
    truncated = total_sitemap_urls > max_urls
    if truncated:
        urls = urls[:max_urls]

    # Build per-call kwargs -- only include optional params when explicitly set
    per_call_kwargs: dict = {"cache_mode": resolved_cache}
    if page_timeout is not None:
        per_call_kwargs["page_timeout"] = page_timeout * 1000
    if css_selector is not None:
        per_call_kwargs["css_selector"] = css_selector
    if target_elements is not None:
        per_call_kwargs["target_elements"] = target_elements
    if excluded_selector is not None:
        per_call_kwargs["excluded_selector"] = excluded_selector
    if wait_for is not None:
        per_call_kwargs["wait_for"] = wait_for
    if js_code is not None:
        per_call_kwargs["js_code"] = js_code
    if user_agent is not None:
        per_call_kwargs["user_agent"] = user_agent
    if word_count_threshold is not None:
        per_call_kwargs["word_count_threshold"] = word_count_threshold
    if query is not None:
        per_call_kwargs["query"] = query

    app: AppContext = ctx.request_context.lifespan_context
    run_cfg = build_run_config(app.profile_manager, profile, **per_call_kwargs)

    rate_limiter = RateLimiter(base_delay=(delay, delay)) if delay > 0 else None
    dispatcher = SemaphoreDispatcher(
        semaphore_count=max_concurrent,
        rate_limiter=rate_limiter,
        # NO monitor -- CrawlerMonitor uses Rich Console -> stdout corruption
    )

    # Heartbeat while the batch runs; see the note in crawl_many for why this
    # is a heartbeat rather than per-page streaming progress.
    results = await _await_with_heartbeat(
        _require_crawler(app).arun_many(
            urls=urls,
            config=run_cfg,
            dispatcher=dispatcher,
        ),
        ctx,
        f"Crawling {len(urls)} sitemap URLs",
    )

    note = None
    if truncated:
        note = (
            f"Sitemap contained {total_sitemap_urls} URLs; crawled the first "
            f"{max_urls} (max_urls limit)."
        )

    if output_dir:
        return _persist_results(
            results,
            output_dir,
            note=note,
            include_links=include_links,
            include_tables=include_tables,
        )
    return _batch_result(
        results,
        note=note,
        include_links=include_links,
        include_tables=include_tables,
    )


def _preflight_playwright() -> None:
    """Warn early when the Chromium build is missing. Never exits.

    Catches the most common install-time failure: `uv sync` upgraded Playwright,
    but the cached Chromium at ms-playwright/chromium-<N> is stale or missing.

    This used to exit(1) with a clean stderr message, on the theory that it beat
    a 60-line anyio traceback. It does — but both are invisible in practice.
    Exiting before the stdio transport opens means the MCP client reports only
    "failed to connect", and the remediation line sits in a connect log the user
    never opens. The server now starts anyway and reports the condition through
    ping and the crawl tools, where an agent will actually read it, while the
    lifespan repairs the browser in the background.
    """
    ok, detail = _chromium_status()
    if ok:
        return

    sys.stderr.write(
        "WARNING: Playwright Chromium binary is missing or stale.\n"
        "This commonly happens after `uv sync` upgrades Playwright to a new version.\n"
        f"Fix:  uv run crawl4ai-setup   (or call the repair_browser tool; auto-repair "
        f"runs at startup unless {AUTO_REPAIR_ENV}=0)\n"
        f"Details: {detail}\n"
    )


def main() -> None:
    """Entry point for `uv run python -m crawl4ai_mcp.server` and the crawl4ai-mcp script.

    Do NOT wrap mcp.run() in asyncio.run() — MCPServer manages the event loop
    internally via anyio. Wrapping causes a 'cannot run nested event loop' error.
    """
    _preflight_playwright()
    mcp.run()  # stdio transport is the default


if __name__ == "__main__":
    main()
