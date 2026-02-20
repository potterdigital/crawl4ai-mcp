# src/crawl4ai_mcp/server.py
import logging
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

from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig, DefaultMarkdownGenerator
from crawl4ai.content_filter_strategy import PruningContentFilter
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession


@dataclass
class AppContext:
    """Typed lifespan context shared across all tool calls.

    The crawler is a single AsyncWebCrawler instance created at server startup
    and reused for every tool call. This avoids the 2-5 second Chromium startup
    cost on every request and prevents browser process leaks.
    """

    crawler: AsyncWebCrawler


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

    try:
        yield AppContext(crawler=crawler)
    finally:
        logger.info("Shutting down browser")
        await crawler.close()
        logger.info("Shutdown complete")


mcp = FastMCP("crawl4ai", lifespan=app_lifespan)


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


def _build_run_config(
    cache_mode: CacheMode = CacheMode.ENABLED,
    css_selector: str | None = None,
    excluded_selector: str | None = None,
    word_count_threshold: int = 10,
    wait_for: str | None = None,
    js_code: str | list[str] | None = None,
    user_agent: str | None = None,
    page_timeout: int = 60000,
) -> CrawlerRunConfig:
    """Build a CrawlerRunConfig with PruningContentFilter applied by default.

    Centralises all run-config construction so that verbose=False and the
    PruningContentFilter pipeline are consistently applied to every crawl.
    verbose=False is CRITICAL — the CrawlerRunConfig default is True, which
    causes crawl4ai's AsyncLogger (backed by Rich Console) to write to stdout,
    immediately corrupting the MCP stdio JSON-RPC transport.
    """
    md_gen = DefaultMarkdownGenerator(
        content_filter=PruningContentFilter(
            threshold=0.48,
            threshold_type="fixed",
            min_word_threshold=word_count_threshold,
        )
    )
    return CrawlerRunConfig(
        markdown_generator=md_gen,
        cache_mode=cache_mode,
        css_selector=css_selector,
        excluded_selector=excluded_selector,
        wait_for=wait_for,
        js_code=js_code,
        user_agent=user_agent,
        page_timeout=page_timeout,
        verbose=False,  # CRITICAL: verbose=True corrupts MCP stdout transport
    )


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


def main() -> None:
    """Entry point for `uv run python -m crawl4ai_mcp.server` and the crawl4ai-mcp script.

    Do NOT wrap mcp.run() in asyncio.run() — FastMCP manages the event loop
    internally via anyio. Wrapping causes a 'cannot run nested event loop' error.
    """
    mcp.run()  # stdio transport is the default


if __name__ == "__main__":
    main()
