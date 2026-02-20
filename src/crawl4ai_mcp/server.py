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

from crawl4ai import AsyncWebCrawler, BrowserConfig
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
