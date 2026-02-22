# src/crawl4ai_mcp/server.py
import asyncio
import gzip
import importlib.metadata
import logging
import os
import re
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
from dataclasses import dataclass

from crawl4ai import (
    AsyncWebCrawler,
    BrowserConfig,
    CacheMode,
    CrawlerRunConfig,
    JsonCssExtractionStrategy,
    LLMConfig,
    LLMExtractionStrategy,
)
from crawl4ai.async_dispatcher import SemaphoreDispatcher
from crawl4ai.deep_crawling import (
    BFSDeepCrawlStrategy,
    FilterChain,
    URLPatternFilter,
)
import httpx
from mcp.server.fastmcp import Context, FastMCP
from packaging.version import Version
from mcp.server.session import ServerSession

from crawl4ai_mcp.profiles import ProfileManager, build_run_config


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
    """

    crawler: AsyncWebCrawler
    profile_manager: ProfileManager
    sessions: dict[str, float]


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    """Initialize AsyncWebCrawler once at server startup; close at shutdown.

    Uses explicit crawler.start() / crawler.close() rather than `async with
    AsyncWebCrawler()` because the lifespan function is itself the context manager.
    The finally block guarantees cleanup even if a tool raises an unhandled exception.
    """
    logger.info("crawl4ai MCP server starting — initializing browser")

    browser_cfg = BrowserConfig(
        headless=True,
        verbose=False,  # CRITICAL: verbose=True outputs to stdout, corrupting MCP transport
        extra_args=[
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--no-sandbox",
        ],
    )
    crawler = AsyncWebCrawler(config=browser_cfg)
    await crawler.start()
    logger.info("Browser ready — crawl4ai MCP server is operational")

    profile_manager = ProfileManager()
    logger.info("Loaded %d profile(s): %s", len(profile_manager.names), profile_manager.names)

    # Fire-and-forget version check — never blocks server readiness
    asyncio.create_task(_startup_version_check())

    app_ctx = AppContext(crawler=crawler, profile_manager=profile_manager, sessions={})
    try:
        yield app_ctx
    finally:
        # Clean up active sessions before closing browser
        for sid in list(app_ctx.sessions.keys()):
            try:
                await crawler.crawler_strategy.kill_session(sid)
            except Exception:
                pass
        logger.info("Shutting down browser")
        await crawler.close()
        logger.info("Shutdown complete")


mcp = FastMCP("crawl4ai", lifespan=app_lifespan)


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


def _format_multi_results(results: list, include_content: bool = True) -> str:
    """Format a list of CrawlResult objects into a structured string for Claude.

    Always includes both successes and failures — never discards successful results
    because of individual URL errors. This helper is shared by crawl_many,
    deep_crawl, and crawl_sitemap.

    Args:
        results: List of CrawlResult objects from arun_many or arun (deep crawl).
        include_content: Whether to include markdown content for successes.
    """
    successes = [r for r in results if r.success]
    failures = [r for r in results if not r.success]

    parts = [f"Crawled {len(successes)} of {len(results)} URLs successfully.\n"]

    for result in successes:
        depth_info = ""
        if result.metadata and isinstance(result.metadata, dict) and "depth" in result.metadata:
            depth_info = f" (depth: {result.metadata['depth']})"
        parent_info = ""
        if result.metadata and isinstance(result.metadata, dict) and result.metadata.get("parent_url"):
            parent_info = f"\nParent: {result.metadata['parent_url']}"

        header = f"## {result.url}{depth_info}{parent_info}"

        if include_content:
            md = result.markdown
            content = (md.fit_markdown or md.raw_markdown) if md else ""
            parts.append(f"{header}\n\n{content}\n")
        else:
            parts.append(f"{header}\n")

    if failures:
        parts.append(f"\n## Failed URLs ({len(failures)})\n")
        for result in failures:
            parts.append(f"- {result.url}: {result.error_message}")

    return "\n".join(parts)


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


@mcp.tool()
async def ping(ctx: Context[ServerSession, AppContext]) -> str:
    """Verify the MCP server is running and the browser is ready.

    Returns 'ok' if the server is healthy. Returns an error description if
    the crawler context is unavailable or the browser has crashed.
    """
    try:
        app: AppContext = ctx.request_context.lifespan_context
        if app.crawler is None:
            return "error: crawler not initialized"
        return "ok"
    except Exception as e:
        logger.error("ping failed: %s", e, exc_info=True)
        return f"error: {e}"


@mcp.tool()
async def list_profiles(ctx: Context[ServerSession, AppContext]) -> str:
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


@mcp.tool()
async def check_update(ctx: Context[ServerSession, AppContext]) -> str:
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
        return (
            f"Version check failed\n"
            f"Installed: {installed}\n"
            f"Error: {exc}"
        )

    if Version(latest) <= Version(installed):
        return (
            f"crawl4ai is up to date\n"
            f"Installed: {installed}\n"
            f"Latest: {latest}"
        )

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


@mcp.tool()
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
    ctx: Context[ServerSession, AppContext] = None,
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

    result = await _crawl_with_overrides(app.crawler, url, run_cfg, headers, cookies)

    if not result.success:
        return _format_crawl_error(url, result)

    # Track session if session_id was provided and crawl succeeded
    if session_id and session_id not in app.sessions:
        app.sessions[session_id] = time.time()

    md = result.markdown
    content = (md.fit_markdown or md.raw_markdown) if md else ""
    return content


@mcp.tool()
async def create_session(
    session_id: str | None = None,
    url: str | None = None,
    cookies: list | None = None,
    headers: dict | None = None,
    ctx: Context[ServerSession, AppContext] = None,
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
        result = await _crawl_with_overrides(app.crawler, url, config, headers, cookies)

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
            await _crawl_with_overrides(app.crawler, "about:blank", config, None, cookies)
        app.sessions[sid] = time.time()
        return f"Session created: {sid}"


@mcp.tool()
async def list_sessions(
    ctx: Context[ServerSession, AppContext] = None,
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


@mcp.tool()
async def destroy_session(
    session_id: str,
    ctx: Context[ServerSession, AppContext] = None,
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
        await app.crawler.crawler_strategy.kill_session(session_id)
    except Exception as exc:
        logger.warning("Error killing session %s: %s", session_id, exc)
    del app.sessions[session_id]
    return f"Session destroyed: {session_id}"


@mcp.tool()
async def crawl_many(
    urls: list[str],
    max_concurrent: int = 10,
    profile: str | None = None,
    cache_mode: str = "enabled",
    css_selector: str | None = None,
    excluded_selector: str | None = None,
    wait_for: str | None = None,
    js_code: str | None = None,
    user_agent: str | None = None,
    page_timeout: int = 60,
    word_count_threshold: int = 10,
    ctx: Context[ServerSession, AppContext] = None,
) -> str:
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

    logger.info("crawl_many: %d URLs (max_concurrent=%d, profile=%s)", len(urls), max_concurrent, profile)

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

    dispatcher = SemaphoreDispatcher(
        semaphore_count=max_concurrent,
        # NO monitor — CrawlerMonitor uses Rich Console -> stdout corruption
        # NO rate_limiter — fast batch crawl by default
    )

    results = await app.crawler.arun_many(
        urls=urls,
        config=run_cfg,
        dispatcher=dispatcher,
    )

    return _format_multi_results(results)


@mcp.tool()
async def extract_structured(
    url: str,
    schema: dict,
    instruction: str,
    provider: str = "openai/gpt-4o-mini",
    css_selector: str | None = None,
    wait_for: str | None = None,
    js_code: str | None = None,
    page_timeout: int = 60,
    ctx: Context[ServerSession, AppContext] = None,
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
    result = await _crawl_with_overrides(app.crawler, url, run_cfg)

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


@mcp.tool()
async def extract_css(
    url: str,
    schema: dict,
    css_selector: str | None = None,
    wait_for: str | None = None,
    js_code: str | None = None,
    page_timeout: int = 60,
    ctx: Context[ServerSession, AppContext] = None,
) -> str:
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
    result = await _crawl_with_overrides(app.crawler, url, run_cfg)

    if not result.success:
        return _format_crawl_error(url, result)

    if not result.extracted_content or result.extracted_content == "[]":
        return (
            "No data extracted\n"
            f"URL: {url}\n"
            "The CSS selectors in the schema did not match any elements on the page.\n"
            "Verify that baseSelector and field selectors are correct for this page's HTML structure."
        )

    return result.extracted_content


@mcp.tool()
async def deep_crawl(
    url: str,
    max_depth: int = 3,
    max_pages: int = 100,
    scope: str = "same-domain",
    include_pattern: str | None = None,
    exclude_pattern: str | None = None,
    profile: str | None = None,
    cache_mode: str = "enabled",
    css_selector: str | None = None,
    excluded_selector: str | None = None,
    wait_for: str | None = None,
    js_code: str | None = None,
    user_agent: str | None = None,
    page_timeout: int = 60,
    word_count_threshold: int = 10,
    ctx: Context[ServerSession, AppContext] = None,
) -> str:
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
        "deep_crawl: %s (depth=%d, max_pages=%d, scope=%s)",
        url, max_depth, max_pages, scope,
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

    # When deep_crawl_strategy is set, arun() returns List[CrawlResult]
    results = await app.crawler.arun(url=url, config=run_cfg)

    return _format_multi_results(results)


@mcp.tool()
async def crawl_sitemap(
    sitemap_url: str,
    max_urls: int = 500,
    max_concurrent: int = 10,
    profile: str | None = None,
    cache_mode: str = "enabled",
    css_selector: str | None = None,
    excluded_selector: str | None = None,
    wait_for: str | None = None,
    js_code: str | None = None,
    user_agent: str | None = None,
    page_timeout: int = 60,
    word_count_threshold: int = 10,
    ctx: Context[ServerSession, AppContext] = None,
) -> str:
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
        "crawl_sitemap: %s (max_urls=%d, max_concurrent=%d)",
        sitemap_url, max_urls, max_concurrent,
    )

    # Fetch and parse sitemap XML via httpx (not the browser)
    try:
        urls = await _fetch_sitemap_urls(sitemap_url)
    except (httpx.HTTPError, ET.ParseError) as e:
        return (
            f"Sitemap fetch failed\n"
            f"URL: {sitemap_url}\n"
            f"Error: {e}"
        )

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

    dispatcher = SemaphoreDispatcher(
        semaphore_count=max_concurrent,
        # NO monitor -- CrawlerMonitor uses Rich Console -> stdout corruption
    )

    results = await app.crawler.arun_many(
        urls=urls,
        config=run_cfg,
        dispatcher=dispatcher,
    )

    output = _format_multi_results(results)
    if truncated:
        output = (
            f"Note: Sitemap contained {total_sitemap_urls} URLs; "
            f"crawled first {max_urls} (max_urls limit).\n\n{output}"
        )
    return output


def main() -> None:
    """Entry point for `uv run python -m crawl4ai_mcp.server` and the crawl4ai-mcp script.

    Do NOT wrap mcp.run() in asyncio.run() — FastMCP manages the event loop
    internally via anyio. Wrapping causes a 'cannot run nested event loop' error.
    """
    mcp.run()  # stdio transport is the default


if __name__ == "__main__":
    main()
