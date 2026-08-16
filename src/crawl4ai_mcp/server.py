# src/crawl4ai_mcp/server.py
import asyncio
import gzip
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
    LLMConfig,
    LLMExtractionStrategy,
)
from crawl4ai.async_dispatcher import RateLimiter, SemaphoreDispatcher
from crawl4ai.deep_crawling import (
    BFSDeepCrawlStrategy,
    FilterChain,
    URLPatternFilter,
)
import httpx
from mcp.server.mcpserver import Context, MCPServer
from mcp.types import ToolAnnotations
from packaging.version import Version
from pydantic import BaseModel

from crawl4ai_mcp.profiles import ProfileManager, build_run_config


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
    return BrowserConfig(
        headless=True,
        verbose=False,  # CRITICAL: verbose=True outputs to stdout, corrupting MCP transport
        extra_args=[
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--no-sandbox",
        ],
    )


async def _start_crawler() -> tuple[AsyncWebCrawler | None, str]:
    """Create and start a crawler. Returns (crawler, error_detail)."""
    crawler = AsyncWebCrawler(config=_build_browser_config())
    try:
        await crawler.start()
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


def _format_crawl_error(url: str, result) -> str:
    """Convert a failed CrawlResult into a structured error string for Claude.

    This pattern is used by all crawl tools in subsequent phases. Returning a
    structured string (rather than raising) lets Claude reason about the failure
    and decide how to proceed.
    """
    return (
        f"Crawl failed\n"
        f"URL: {url}\n"
        f"HTTP status: {result.status_code}\n"
        f"Error: {result.error_message}"
    )


def _check_api_key(provider: str) -> str | None:
    """Validate that the expected API key env var is set for the given provider.

    Returns a structured error string if the key is missing, or None if the key
    is present, the provider is local (e.g. ollama), or the provider is unknown
    (let litellm handle unknown providers at call time).
    """
    prefix = provider.split("/")[0].lower()
    env_var = PROVIDER_ENV_VARS.get(prefix)
    if env_var is None:
        # Provider is local (ollama) or unknown — no env var to check
        return None
    if not os.environ.get(env_var):
        return (
            f"API key not set\n"
            f"Provider: {provider}\n"
            f"Required environment variable: {env_var}\n"
            f"Set it with: export {env_var}=your-key-here"
        )
    return None


class PageResult(BaseModel):
    """One page from a multi-page crawl."""

    url: str
    success: bool
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


class ExtractionResult(BaseModel):
    """Result of a CSS-selector extraction."""

    url: str
    count: int
    """Number of items matched by the schema's baseSelector."""
    items: list[dict]
    """Extracted records, shaped by the caller's schema. Empty when nothing matched."""
    error: str | None = None
    """Why extraction produced nothing. None on success."""


def _page_results(results: list, include_content: bool = True) -> list[PageResult]:
    """Convert crawl4ai CrawlResult objects into the wire model.

    Shared by crawl_many, deep_crawl, and crawl_sitemap. Successes come first
    so the useful half of a partial crawl is not buried under failures.
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
                    markdown=content if include_content else None,
                    depth=meta.get("depth"),
                    parent_url=meta.get("parent_url"),
                )
            )
        else:
            pages.append(
                PageResult(
                    url=result.url,
                    success=False,
                    error=result.error_message,
                    depth=meta.get("depth"),
                    parent_url=meta.get("parent_url"),
                )
            )
    return pages


def _batch_result(results: list, note: str | None = None) -> CrawlBatchResult:
    """Build the structured result returned by every multi-page crawl tool."""
    return CrawlBatchResult(
        crawled=sum(1 for r in results if r.success),
        total=len(results),
        pages=_page_results(results),
        note=note,
    )


def _sanitize_filename(url: str) -> str:
    """Convert a URL to a safe filename stem (no extension).

    Strips scheme, replaces non-alphanumeric chars with underscores,
    collapses runs, and trims to 200 chars.
    """
    name = re.sub(r"^https?://", "", url)
    name = re.sub(r"[^a-zA-Z0-9]+", "_", name)
    name = name.strip("_")[:200]
    return name or "page"


def _persist_results(
    results: list, output_dir: str, note: str | None = None
) -> CrawlBatchResult:
    """Write per-page .md files and a manifest.json to output_dir.

    Returns the same CrawlBatchResult shape as an inline crawl, with each
    page's `file` set and its `markdown` left None: the content is on disk,
    and repeating it in the result would defeat the point of output_dir.
    """
    os.makedirs(output_dir, exist_ok=True)

    successes = [r for r in results if r.success]
    failures = [r for r in results if not r.success]
    manifest_entries: list[dict] = []

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

    # Same shape as an inline crawl, but pointing at files instead of carrying
    # content. include_content=False is what leaves markdown None.
    pages = _page_results(results, include_content=False)
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


SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


async def _fetch_sitemap_urls(sitemap_url: str) -> list[str]:
    """Fetch and parse a sitemap XML, returning all <loc> URLs.

    Handles:
    - Regular sitemaps (<urlset> with <url><loc>)
    - Sitemap indexes (<sitemapindex> with <sitemap><loc>) -- recursively resolved
    - Gzipped sitemaps (.xml.gz) -- automatically decompressed
    - Sitemaps with or without XML namespace prefix
    """
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        resp = await client.get(sitemap_url)
        resp.raise_for_status()

    content = resp.content
    if sitemap_url.endswith(".gz"):
        content = gzip.decompress(content)

    root = ET.fromstring(content)

    # Check if this is a sitemap index
    sub_sitemaps = root.findall("sm:sitemap/sm:loc", SITEMAP_NS)
    if sub_sitemaps:
        urls: list[str] = []
        for loc_elem in sub_sitemaps:
            sub_urls = await _fetch_sitemap_urls(loc_elem.text.strip())
            urls.extend(sub_urls)
        return urls

    # Regular sitemap -- extract <url><loc> entries
    # Try with namespace first, then without (some sitemaps omit namespace)
    locs = root.findall("sm:url/sm:loc", SITEMAP_NS)
    if not locs:
        locs = root.findall("url/loc")
    return [loc.text.strip() for loc in locs if loc.text]


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
    fallback = "Changelog: https://github.com/unclecode/crawl4ai/blob/main/CHANGELOG.md"
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
            return fallback

        section = match.group(1)
        # Keep category headers (### ) and first-level bullets (- **)
        lines = []
        for line in section.splitlines():
            stripped = line.strip()
            if stripped.startswith("### ") or stripped.startswith("- **"):
                lines.append(stripped)
        if not lines:
            return fallback

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


async def _crawl_with_overrides(
    crawler: AsyncWebCrawler,
    url: str,
    config: CrawlerRunConfig,
    headers: dict | None = None,
    cookies: list | None = None,
):
    """Run arun with per-request header and cookie injection via Playwright hooks.

    CrawlerRunConfig in crawl4ai 0.8.0 has no headers or cookies parameters
    (those are BrowserConfig-level and thus global). This helper injects them
    per-request via Playwright strategy hooks immediately before arun(), then
    clears the hooks in a finally block — even if arun() raises — to prevent
    hook leakage into subsequent tool calls.
    """
    strategy = crawler.crawler_strategy

    if headers:

        async def before_goto(page, context, url, config, **kwargs):
            await page.set_extra_http_headers(headers)

        strategy.set_hook("before_goto", before_goto)

    if cookies:

        async def on_page_context_created(page, context, **kwargs):
            await context.add_cookies(cookies)

        strategy.set_hook("on_page_context_created", on_page_context_created)

    try:
        return await crawler.arun(url=url, config=config)
    finally:
        if headers:
            strategy.set_hook("before_goto", None)
        if cookies:
            strategy.set_hook("on_page_context_created", None)


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
            for k, v in sorted(cfg.items()):
                lines.append(f"  {k}: {v}")
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
    cache_mode: str = "enabled",
    css_selector: str | None = None,
    excluded_selector: str | None = None,
    wait_for: str | None = None,
    js_code: str | None = None,
    user_agent: str | None = None,
    headers: dict | None = None,
    cookies: list | None = None,
    page_timeout: int = 60,
    word_count_threshold: int = 10,
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

        cache_mode: Controls crawl4ai's cache read/write behaviour.
            - "enabled"    — use cache if available, fetch and store on miss (default)
            - "bypass"     — always fetch fresh; do not read or write cache
            - "disabled"   — fetch fresh; no cache read or write for this session
            - "read_only"  — return cached result only; fail if not cached
            - "write_only" — fetch fresh and overwrite cache; ignore existing cached

        css_selector: Restrict extraction to elements matching this CSS selector
            (include scope). Example: "article.main-content" extracts only the
            article element. Without this, the full page body is extracted.

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

        user_agent: Override the browser User-Agent string for this request only.
            Example: "Mozilla/5.0 (compatible; MyBot/1.0)"

        headers: Dict of custom HTTP headers to send with the request. Applied via
            Playwright page hooks; cleared after the request to avoid leaking into
            subsequent calls. Example: {"Authorization": "Bearer token", "X-Custom": "val"}

        cookies: List of cookie dicts to send with the request. Each cookie must
            have at minimum: name, value, domain. Optional fields: path, expires,
            httpOnly, secure, sameSite.
            Example: [{"name": "session", "value": "abc123", "domain": "example.com"}]

        page_timeout: Maximum seconds to wait for the page to load before timing
            out (default 60). Converted to milliseconds internally.

        word_count_threshold: Minimum word count for a content block to survive
            PruningContentFilter (default 10). Lower values retain more short
            blocks; higher values prune more aggressively.
    """
    _CACHE_MAP = {
        "enabled": CacheMode.ENABLED,
        "bypass": CacheMode.BYPASS,
        "disabled": CacheMode.DISABLED,
        "read_only": CacheMode.READ_ONLY,
        "write_only": CacheMode.WRITE_ONLY,
    }
    resolved_cache = _CACHE_MAP.get(cache_mode, CacheMode.ENABLED)
    if cache_mode not in _CACHE_MAP:
        logger.warning("Unknown cache_mode %r — defaulting to 'enabled'", cache_mode)

    logger.info("crawl_url: %s (cache=%s, profile=%s)", url, cache_mode, profile)

    # Build per-call kwargs — only include optional params when explicitly set
    # so that profile values are not silently overridden by None/default sentinel values.
    # Convert page_timeout from seconds (tool interface) to ms (CrawlerRunConfig native unit).
    per_call_kwargs: dict = {
        "cache_mode": resolved_cache,
        "page_timeout": page_timeout * 1000,
    }
    if css_selector is not None:
        per_call_kwargs["css_selector"] = css_selector
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
    if word_count_threshold != 10:
        per_call_kwargs["word_count_threshold"] = word_count_threshold

    app: AppContext = ctx.request_context.lifespan_context
    run_cfg = build_run_config(app.profile_manager, profile, **per_call_kwargs)

    result = await _crawl_with_overrides(
        _require_crawler(app), url, run_cfg, headers, cookies
    )

    if not result.success:
        return _format_crawl_error(url, result)

    # Track session if session_id was provided and crawl succeeded
    if session_id and session_id not in app.sessions:
        app.sessions[session_id] = time.time()

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
        # Create session page without navigating
        if cookies:
            # Need to do a minimal crawl to inject cookies via hooks
            config = build_run_config(
                app.profile_manager,
                None,
                session_id=sid,
                cache_mode=CacheMode.BYPASS,
            )
            # Use about:blank as a no-op navigation target for cookie injection
            await _crawl_with_overrides(
                _require_crawler(app), "about:blank", config, None, cookies
            )
        app.sessions[sid] = time.time()
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

    lines = ["Active sessions:"]
    now = time.time()
    for sid, created in sorted(app.sessions.items()):
        age_mins = (now - created) / 60
        lines.append(f"  - {sid} (created {age_mins:.0f} min ago)")
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
    profile: str | None = None,
    cache_mode: str = "enabled",
    css_selector: str | None = None,
    excluded_selector: str | None = None,
    wait_for: str | None = None,
    js_code: str | None = None,
    user_agent: str | None = None,
    page_timeout: int = 60,
    word_count_threshold: int = 10,
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

        profile: Name of a crawl profile to use as base configuration.
            Per-call parameters take precedence over profile values.
            Use list_profiles to see available profiles.

        cache_mode: Controls crawl4ai's cache read/write behaviour.
            - "enabled"    — use cache if available, fetch and store on miss (default)
            - "bypass"     — always fetch fresh; do not read or write cache
            - "disabled"   — fetch fresh; no cache read or write for this session
            - "read_only"  — return cached result only; fail if not cached
            - "write_only" — fetch fresh and overwrite cache; ignore existing cached

        css_selector: Restrict extraction to elements matching this CSS selector
            (include scope). Applied to ALL URLs in the batch.

        excluded_selector: Exclude elements matching this CSS selector from
            extraction. Applied to ALL URLs in the batch.

        wait_for: Wait until a CSS selector or JavaScript condition is met before
            extracting content. Applied to ALL URLs in the batch.

        js_code: JavaScript to execute in each page after load and before
            extraction. Applied to ALL URLs in the batch.

        user_agent: Override the browser User-Agent string for all requests.

        page_timeout: Maximum seconds to wait for each page to load (default 60).

        word_count_threshold: Minimum word count for a content block to survive
            PruningContentFilter (default 10).
    """
    _CACHE_MAP = {
        "enabled": CacheMode.ENABLED,
        "bypass": CacheMode.BYPASS,
        "disabled": CacheMode.DISABLED,
        "read_only": CacheMode.READ_ONLY,
        "write_only": CacheMode.WRITE_ONLY,
    }
    resolved_cache = _CACHE_MAP.get(cache_mode, CacheMode.ENABLED)
    if cache_mode not in _CACHE_MAP:
        logger.warning("Unknown cache_mode %r — defaulting to 'enabled'", cache_mode)

    logger.info(
        "crawl_many: %d URLs (max_concurrent=%d, delay=%.1f, profile=%s)",
        len(urls),
        max_concurrent,
        delay,
        profile,
    )

    # Build per-call kwargs — only include optional params when explicitly set
    per_call_kwargs: dict = {
        "cache_mode": resolved_cache,
        "page_timeout": page_timeout * 1000,
    }
    if css_selector is not None:
        per_call_kwargs["css_selector"] = css_selector
    if excluded_selector is not None:
        per_call_kwargs["excluded_selector"] = excluded_selector
    if wait_for is not None:
        per_call_kwargs["wait_for"] = wait_for
    if js_code is not None:
        per_call_kwargs["js_code"] = js_code
    if user_agent is not None:
        per_call_kwargs["user_agent"] = user_agent
    if word_count_threshold != 10:
        per_call_kwargs["word_count_threshold"] = word_count_threshold

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
        return _persist_results(results, output_dir)
    return _batch_result(results)


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
    css_selector: str | None = None,
    wait_for: str | None = None,
    js_code: str | None = None,
    page_timeout: int = 60,
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
        provider: LLM provider and model in litellm format (default:
            "openai/gpt-4o-mini"). Examples: "anthropic/claude-sonnet-4-20250514",
            "gemini/gemini-2.0-flash". The API key is read from the
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
    strategy = LLMExtractionStrategy(
        llm_config=llm_config,
        schema=schema,
        extraction_type="schema",
        instruction=instruction,
        input_format="fit_markdown",
        verbose=False,  # CRITICAL: protect MCP transport
    )

    # Build CrawlerRunConfig directly (not via build_run_config) —
    # extraction tools don't need markdown_generator or profile merging.
    run_cfg = CrawlerRunConfig(
        extraction_strategy=strategy,
        page_timeout=page_timeout * 1000,
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

    if not result.extracted_content:
        return (
            f"Extraction returned no data\n"
            f"URL: {url}\n"
            f"The LLM did not produce structured output. "
            f"Check that the schema matches the page content."
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
    title="Extract structured JSON with CSS selectors (free)",
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
    css_selector: str | None = None,
    wait_for: str | None = None,
    js_code: str | None = None,
    page_timeout: int = 60,
    ctx: Context[AppContext] = None,
) -> ExtractionResult:
    """Extract structured JSON from a page using CSS selectors (no LLM, no cost).

    Uses crawl4ai's JsonCssExtractionStrategy for deterministic, repeatable
    extraction. No LLM API call is made — this tool is completely free to use.

    Args:
        url: The URL to crawl and extract data from.

        schema: Extraction schema dict defining what to extract. Must contain:
            - "name": A label for the extraction (e.g. "Products")
            - "baseSelector": CSS selector matching each repeating item
              (e.g. "div.product-card")
            - "fields": List of field definitions, each with:
              - "name": Field name in output JSON
              - "selector": CSS selector relative to baseSelector
              - "type": One of "text", "attribute", "html", "regex",
                "list", "nested", "nested_list"
              - "attribute": Required when type is "attribute" (e.g. "href", "src")
              - "transform": Optional, e.g. "strip", "lowercase"
              - "default": Optional default value if selector matches nothing
              - "fields": Required for "nested"/"nested_list"/"list" types
                (recursive field definitions)

            Example:
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

        css_selector: Restrict extraction scope to elements matching this
            CSS selector before applying the extraction schema.

        wait_for: Wait condition before extraction (CSS: "css:#el",
            JS: "js:() => expr"). Useful for dynamically loaded content.

        js_code: JavaScript to execute after page load, before extraction.
            Use to trigger lazy loading or expand collapsed sections.

        page_timeout: Page load timeout in seconds (default 60).
    """
    logger.info("extract_css: %s", url)

    strategy = JsonCssExtractionStrategy(schema, verbose=False)

    # Build CrawlerRunConfig directly (not via build_run_config) —
    # extraction tools don't need markdown_generator or profile merging.
    run_cfg = CrawlerRunConfig(
        extraction_strategy=strategy,
        page_timeout=page_timeout * 1000,
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
                "The CSS selectors in the schema did not match any elements on the "
                "page. Verify that baseSelector and the field selectors are correct "
                "for this page's HTML structure."
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
    include_pattern: str | None = None,
    exclude_pattern: str | None = None,
    delay: float = 0,
    output_dir: str | None = None,
    profile: str | None = None,
    cache_mode: str = "enabled",
    css_selector: str | None = None,
    excluded_selector: str | None = None,
    wait_for: str | None = None,
    js_code: str | None = None,
    user_agent: str | None = None,
    page_timeout: int = 60,
    word_count_threshold: int = 10,
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
              domain (includes subdomains).
            - "same-origin": Same behavior as same-domain.
            - "any": Follow all links including external domains.

        include_pattern: Glob pattern to filter which URLs to follow (e.g.,
            "/docs/*" to only follow documentation links). Only URLs matching
            this pattern will be crawled.

        exclude_pattern: Glob pattern to exclude URLs from following (e.g.,
            "/internal/*" to skip internal links). URLs matching this pattern
            will not be crawled.

        delay: Politeness delay in seconds between page fetches (default 0 —
            no delay). Passed as delay_before_return_html to crawl4ai.

        output_dir: Directory to write per-page .md files and a manifest.json.
            When set, returns a metadata summary (file paths) instead of page
            content. When None (default), returns full content inline.
            Existing files are overwritten without warning when their names
            collide — manifest.json always is. Point this at a directory you
            own, not one holding files you need.

        profile: Named crawl profile for per-page configuration.
        cache_mode: Cache behavior (same as crawl_url).
        css_selector: Restrict extraction to matching elements on each page.
        excluded_selector: Exclude matching elements from extraction.
        wait_for: Wait condition before extracting each page.
        js_code: JavaScript to execute on each page before extraction.
        user_agent: Override User-Agent string.
        page_timeout: Page load timeout in seconds (default 60).
        word_count_threshold: Minimum word count for content blocks (default 10).

    Note:
        Per-request headers and cookies are not supported for deep_crawl in v1.
        Use crawl_url for single pages that need custom headers or cookies.
    """
    _CACHE_MAP = {
        "enabled": CacheMode.ENABLED,
        "bypass": CacheMode.BYPASS,
        "disabled": CacheMode.DISABLED,
        "read_only": CacheMode.READ_ONLY,
        "write_only": CacheMode.WRITE_ONLY,
    }
    resolved_cache = _CACHE_MAP.get(cache_mode, CacheMode.ENABLED)
    if cache_mode not in _CACHE_MAP:
        logger.warning("Unknown cache_mode %r — defaulting to 'enabled'", cache_mode)

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
    filter_chain = FilterChain(filters=filters) if filters else FilterChain()

    # Map scope to include_external
    if scope in ("same-domain", "same-origin"):
        include_external = False
    elif scope == "any":
        include_external = True
    else:
        logger.warning("Unknown scope %r — defaulting to 'same-domain'", scope)
        include_external = False

    # MUST be fresh per call — BFSDeepCrawlStrategy has mutable state
    strategy = BFSDeepCrawlStrategy(
        max_depth=max_depth,
        max_pages=max_pages,
        include_external=include_external,
        filter_chain=filter_chain,
    )

    # Build per-call kwargs — only include optional params when explicitly set
    per_call_kwargs: dict = {
        "cache_mode": resolved_cache,
        "page_timeout": page_timeout * 1000,
        "deep_crawl_strategy": strategy,
    }
    if delay > 0:
        per_call_kwargs["delay_before_return_html"] = delay
    if css_selector is not None:
        per_call_kwargs["css_selector"] = css_selector
    if excluded_selector is not None:
        per_call_kwargs["excluded_selector"] = excluded_selector
    if wait_for is not None:
        per_call_kwargs["wait_for"] = wait_for
    if js_code is not None:
        per_call_kwargs["js_code"] = js_code
    if user_agent is not None:
        per_call_kwargs["user_agent"] = user_agent
    if word_count_threshold != 10:
        per_call_kwargs["word_count_threshold"] = word_count_threshold

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
        key=lambda r: (r.metadata or {}).get("depth", 0)
        if isinstance(r.metadata, dict)
        else 0
    )

    if output_dir:
        return _persist_results(results, output_dir)
    return _batch_result(results)


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
    profile: str | None = None,
    cache_mode: str = "enabled",
    css_selector: str | None = None,
    excluded_selector: str | None = None,
    wait_for: str | None = None,
    js_code: str | None = None,
    user_agent: str | None = None,
    page_timeout: int = 60,
    word_count_threshold: int = 10,
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

        profile: Named crawl profile for per-page configuration.
        cache_mode: Cache behavior (same as crawl_url).
        css_selector: Restrict extraction to matching elements on each page.
        excluded_selector: Exclude matching elements from extraction.
        wait_for: Wait condition before extracting each page.
        js_code: JavaScript to execute on each page before extraction.
        user_agent: Override User-Agent string.
        page_timeout: Page load timeout in seconds (default 60).
        word_count_threshold: Minimum word count for content blocks (default 10).

    Note:
        Per-call headers and cookies are not supported for sitemap crawls.
        Use crawl_url for requests requiring custom headers or cookies.
    """
    _CACHE_MAP = {
        "enabled": CacheMode.ENABLED,
        "bypass": CacheMode.BYPASS,
        "disabled": CacheMode.DISABLED,
        "read_only": CacheMode.READ_ONLY,
        "write_only": CacheMode.WRITE_ONLY,
    }
    resolved_cache = _CACHE_MAP.get(cache_mode, CacheMode.ENABLED)
    if cache_mode not in _CACHE_MAP:
        logger.warning("Unknown cache_mode %r -- defaulting to 'enabled'", cache_mode)

    logger.info(
        "crawl_sitemap: %s (max_urls=%d, max_concurrent=%d, delay=%.1f)",
        sitemap_url,
        max_urls,
        max_concurrent,
        delay,
    )

    # Fetch and parse sitemap XML via httpx (not the browser)
    try:
        urls = await _fetch_sitemap_urls(sitemap_url)
    except (httpx.HTTPError, ET.ParseError) as e:
        return f"Sitemap fetch failed\nURL: {sitemap_url}\nError: {e}"

    if not urls:
        return (
            f"No URLs found in sitemap\n"
            f"URL: {sitemap_url}\n"
            f"The sitemap may be empty or use an unsupported format."
        )

    # Truncate if over max_urls
    total_sitemap_urls = len(urls)
    truncated = total_sitemap_urls > max_urls
    if truncated:
        urls = urls[:max_urls]

    # Build per-call kwargs -- only include optional params when explicitly set
    per_call_kwargs: dict = {
        "cache_mode": resolved_cache,
        "page_timeout": page_timeout * 1000,
    }
    if css_selector is not None:
        per_call_kwargs["css_selector"] = css_selector
    if excluded_selector is not None:
        per_call_kwargs["excluded_selector"] = excluded_selector
    if wait_for is not None:
        per_call_kwargs["wait_for"] = wait_for
    if js_code is not None:
        per_call_kwargs["js_code"] = js_code
    if user_agent is not None:
        per_call_kwargs["user_agent"] = user_agent
    if word_count_threshold != 10:
        per_call_kwargs["word_count_threshold"] = word_count_threshold

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
        return _persist_results(results, output_dir, note=note)
    return _batch_result(results, note=note)


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
