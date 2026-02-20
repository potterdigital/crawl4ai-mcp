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
from dataclasses import dataclass, field

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession


@dataclass
class AppContext:
    """Typed context shared across all tool calls via FastMCP lifespan.

    crawler is added in Plan 01-02. This stub exists so server.py imports
    and runs cleanly for Plan 01-01 verification.
    """

    _placeholder: str = field(default="stub", repr=False)


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    """FastMCP lifespan: runs at server startup and shutdown.

    AsyncWebCrawler singleton is wired in Plan 01-02.
    """
    logger.info("crawl4ai MCP server starting (stub lifespan — Plan 01-01)")
    try:
        yield AppContext()
    finally:
        logger.info("crawl4ai MCP server shutting down")


mcp = FastMCP("crawl4ai", lifespan=app_lifespan)


@mcp.tool()
async def ping(ctx: Context[ServerSession, AppContext]) -> str:
    """Verify the MCP server is running and accepting tool calls.

    Returns 'ok' when healthy. Returns an error description if the
    server context is unavailable.
    """
    try:
        _app: AppContext = ctx.request_context.lifespan_context
        return "ok"
    except Exception as e:
        logger.error("ping failed: %s", e, exc_info=True)
        return f"error: {e}"


def main() -> None:
    """Entry point for `uv run python -m crawl4ai_mcp.server` and the crawl4ai-mcp script."""
    mcp.run()  # stdio transport is the default — do NOT wrap in asyncio.run()


if __name__ == "__main__":
    main()
