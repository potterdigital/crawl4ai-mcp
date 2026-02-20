# src/crawl4ai_mcp/server.py
import logging
import os
import sys

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
    LLMConfig,
    LLMExtractionStrategy,
)
from mcp.server.fastmcp import Context, FastMCP
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
    """

    crawler: AsyncWebCrawler
    profile_manager: ProfileManager


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

    try:
        yield AppContext(crawler=crawler, profile_manager=profile_manager)
    finally:
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
async def crawl_url(
    url: str,
    profile: str | None = None,
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
    if word_count_threshold != 10:
        per_call_kwargs["word_count_threshold"] = word_count_threshold

    app: AppContext = ctx.request_context.lifespan_context
    run_cfg = build_run_config(app.profile_manager, profile, **per_call_kwargs)

    result = await _crawl_with_overrides(app.crawler, url, run_cfg, headers, cookies)

    if not result.success:
        return _format_crawl_error(url, result)

    md = result.markdown
    content = (md.fit_markdown or md.raw_markdown) if md else ""
    return content


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


def main() -> None:
    """Entry point for `uv run python -m crawl4ai_mcp.server` and the crawl4ai-mcp script.

    Do NOT wrap mcp.run() in asyncio.run() — FastMCP manages the event loop
    internally via anyio. Wrapping causes a 'cannot run nested event loop' error.
    """
    mcp.run()  # stdio transport is the default


if __name__ == "__main__":
    main()
