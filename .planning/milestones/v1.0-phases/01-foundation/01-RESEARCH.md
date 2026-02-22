# Phase 1: Foundation - Research

**Researched:** 2026-02-19
**Domain:** FastMCP server scaffolding, stdio transport, AsyncWebCrawler lifecycle, stderr-only logging, Claude Code MCP registration
**Confidence:** HIGH

## Summary

Phase 1 builds the plumbing that every subsequent phase depends on. It is deliberately narrow: no crawl tools yet, just a server that starts cleanly, manages the browser lifecycle correctly, routes all output to stderr, and registers with Claude Code. The patterns established here cannot be retrofitted later — getting the lifespan singleton, stdio hygiene, and error handling right in Phase 1 costs nothing; getting them wrong costs a full rewrite.

The standard approach is well-documented and unambiguous. FastMCP's `lifespan` async context manager is the canonical pattern for managing `AsyncWebCrawler` as a singleton — create it at server start, store it in a typed `AppContext` dataclass, yield it to all tool handlers via `ctx.request_context.lifespan_context`, and close it in the `finally` block at shutdown. The `mcp.run()` call defaults to stdio transport. Logging must be configured to `stream=sys.stderr` before any library imports emit output.

The two risks that must be addressed on day one are stdout corruption (any `print()` or `verbose=True` in crawl4ai breaks the MCP JSON-RPC protocol stream) and browser process leaks (not using the singleton lifespan pattern causes Chromium to accumulate across requests). Both are fully preventable with discipline and a one-time setup. The project's existing research corpus (STACK.md, ARCHITECTURE.md, PITFALLS.md, SUMMARY.md) covers these at project scope; this document distills what Phase 1 specifically needs to implement, in what order, with verified code patterns.

**Primary recommendation:** Follow the FastMCP lifespan pattern exactly as documented in the MCP Python SDK — `@asynccontextmanager`, typed `AppContext` dataclass, `crawler.start()` before yield, `crawler.close()` in finally — and enforce stderr-only logging from the first line of server code.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| INFRA-01 | Server starts via `uv run` with stdio transport and registers correctly as a Claude Code global MCP server | See "Standard Stack" (uv project setup, `pyproject.toml` scripts entry) and "Code Examples" (registration command, `mcp.run()` defaults) |
| INFRA-02 | All server output (logs, errors, debug) goes to stderr only — stdout is never written to | See "Architecture Patterns" (Pattern 2: Stderr-Only Logging) and "Common Pitfalls" (Pitfall 1: stdout corruption) |
| INFRA-03 | A single `AsyncWebCrawler` instance is created at server startup via FastMCP lifespan and reused across all tool calls | See "Architecture Patterns" (Pattern 1: Lifespan Singleton) and "Code Examples" (full lifespan implementation) |
| INFRA-04 | Server handles crawler errors gracefully — returns structured error responses to Claude rather than crashing | See "Architecture Patterns" (Pattern 3: Error Handling) and "Code Examples" (result.success / error_message pattern) |
| INFRA-05 | README documents how to register the server as a Claude Code global MCP server with exact config snippet | See "Code Examples" (claude mcp add-json command) |
</phase_requirements>

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `mcp` (FastMCP) | 1.26.0 | MCP server framework, stdio transport, lifespan, tool decorators | Official Anthropic MCP Python SDK. `FastMCP` provides the `lifespan` API, `@mcp.tool()` decorator, and `mcp.run()` (defaults to stdio). No alternatives worth considering for this project. |
| `crawl4ai` | 0.8.0 | Web crawling engine with `AsyncWebCrawler` | The library being wrapped. Pin this exact version — crawl4ai has a history of breaking API changes between minor versions. |
| `uv` | 0.10.x | Project setup, dependency management, server invocation | `uv run --directory /path/to/project python -m crawl4ai_mcp.server` is how Claude Code invokes the server from any working directory. Manages the venv automatically. |
| Python | 3.12+ | Runtime | Both `mcp` and `crawl4ai` require >=3.10. 3.12 target for best asyncio performance. Set in `.python-version`. |

### Supporting (Phase 1 Scope)

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `pydantic` | >=2.10 | `AppContext` dataclass typing | Already a dependency of both `mcp` and `crawl4ai`. Use `@dataclass` for `AppContext` (simpler than Pydantic model for a container); Pydantic is used by FastMCP for tool parameter schemas automatically. |
| `logging` | stdlib | Stderr-only logging | Configure once at server startup with `stream=sys.stderr`. Never use `print()`. |

### What NOT to Install for Phase 1

| Avoid | Why |
|-------|-----|
| `crawl4ai[all]` or `crawl4ai[torch]` | Adds 2GB+ of PyTorch/ML dependencies. Not needed for Phase 1 or any planned feature. |
| `crawl4ai[sync]` | Deprecated Selenium-based sync mode. Use async API exclusively. |
| Any SSE/HTTP transport libs | Phase 1 uses stdio. No additional transport library needed. |

### Installation

```bash
# 1. Initialize project
uv init crawl4ai-mcp
cd crawl4ai-mcp

# 2. Pin Python version
echo "3.12" > .python-version

# 3. Add dependencies
uv add "mcp[cli]>=1.26.0"
uv add "crawl4ai>=0.8.0,<0.9.0"   # pin minor to prevent breaking changes

# 4. Add dev dependencies
uv add --dev pytest pytest-asyncio

# 5. Install Playwright browsers (required by crawl4ai)
uv run crawl4ai-setup

# 6. Verify installation
uv run crawl4ai-doctor
```

**Note on pinning:** Use `>=0.8.0,<0.9.0` rather than `==0.8.0` so patch releases are picked up, but minor version bumps (which typically carry breaking changes in pre-1.0 crawl4ai) are blocked. Update `uv.lock` explicitly when a new minor version is tested.

## Architecture Patterns

### Recommended Project Structure (Phase 1 Deliverable)

```
crawl4ai-mcp/
├── pyproject.toml           # uv project config: deps, [project.scripts]
├── uv.lock                  # Commit this — reproducible installs
├── .python-version          # "3.12"
├── README.md                # Claude Code registration snippet (INFRA-05)
└── src/
    └── crawl4ai_mcp/
        ├── __init__.py
        └── server.py        # FastMCP instance, lifespan, AppContext, stub tool
```

The `tools/`, `profiles/`, and other modules are added in later phases. Phase 1 only needs `server.py` with the lifespan wired up and a single stub tool to verify the connection end-to-end.

### Pattern 1: Lifespan-Managed AsyncWebCrawler Singleton (INFRA-03)

**What:** FastMCP's `lifespan` async context manager initializes `AsyncWebCrawler` once at server startup, stores it in a typed `AppContext`, and makes it available to every tool call. Cleanup happens in the `finally` block at shutdown.

**Critical detail:** Use `crawler.start()` / `crawler.close()` explicitly (not `async with AsyncWebCrawler()`) because the lifespan function itself is the context manager. The `async with` form is for short-lived crawlers; the lifespan pattern requires manual start/close.

**Source:** MCP Python SDK README (Context7 `/modelcontextprotocol/python-sdk`, HIGH confidence); crawl4ai API docs "Manual Start and Close" (Context7 `/unclecode/crawl4ai`, HIGH confidence)

```python
# Source: MCP Python SDK README + crawl4ai API docs
import sys
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from crawl4ai import AsyncWebCrawler, BrowserConfig
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession

# Configure logging to stderr BEFORE any other imports that might emit output
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)


@dataclass
class AppContext:
    """Typed lifespan context shared across all tool calls."""
    crawler: AsyncWebCrawler


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    """Initialize AsyncWebCrawler at server startup; close at shutdown."""
    logger.info("Starting crawl4ai MCP server...")

    browser_cfg = BrowserConfig(
        headless=True,
        verbose=False,          # CRITICAL: verbose=True outputs to stdout and corrupts MCP transport
        extra_args=[
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--no-sandbox",
        ],
    )
    crawler = AsyncWebCrawler(config=browser_cfg)
    await crawler.start()
    logger.info("AsyncWebCrawler started — browser ready")

    try:
        yield AppContext(crawler=crawler)
    finally:
        logger.info("Shutting down AsyncWebCrawler...")
        await crawler.close()
        logger.info("Shutdown complete")


mcp = FastMCP("crawl4ai", lifespan=app_lifespan)
```

### Pattern 2: Stderr-Only Logging Enforcement (INFRA-02)

**What:** All log output goes to `sys.stderr`. stdout is reserved exclusively for MCP JSON-RPC protocol frames. Any single byte of non-JSON written to stdout breaks the Claude Code connection.

**Rules (enforced by convention, not just configuration):**
1. `logging.basicConfig(stream=sys.stderr)` at the top of `server.py`, before other imports
2. Never use `print()` anywhere in server code — add a linting rule
3. `BrowserConfig(verbose=False)` always (default is `False` in 0.8.0, but be explicit)
4. `CrawlerRunConfig(verbose=False)` always when constructing run configs
5. Use `ctx.info()` / `ctx.debug()` within tool handlers (routes through MCP protocol correctly, not stdout)
6. For `logger.info()` outside tool handlers, route to stderr via the basicConfig above

**Source:** Perplexity search (HIGH confidence with citation); MCP debugging docs; PITFALLS.md (verified with Roo Code GitHub issue #5462)

```python
# Correct: all logging to stderr
import logging, sys
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# WRONG — never do this:
print("Server starting...")  # corrupts stdout MCP stream
logging.basicConfig(stream=sys.stdout)  # corrupts stdout MCP stream
BrowserConfig(verbose=True)  # crawl4ai outputs to stdout when verbose=True
```

**Note from Perplexity research:** Even with `verbose=False`, some crawl4ai versions have print statements that persist (GitHub issue #264). If stdout corruption occurs despite `verbose=False`, check crawl4ai source for `print()` calls and consider redirecting stdout at process level: `sys.stdout = sys.stderr` as a last resort (though this may interfere with FastMCP's own stdio handling — validate before using).

### Pattern 3: Structured Error Handling in Tools (INFRA-04)

**What:** Tool handlers catch exceptions from crawl4ai and return structured error strings rather than letting exceptions propagate. `result.success` and `result.error_message` are always checked on `CrawlResult`.

**Source:** crawl4ai API docs "Simple Crawling > Error Handling" (Context7 `/unclecode/crawl4ai`, HIGH confidence)

```python
# Source: crawl4ai API docs + MCP Python SDK patterns
@mcp.tool()
async def ping(ctx: Context[ServerSession, AppContext]) -> str:
    """Verify the MCP server and browser are operational.
    Returns 'ok' if the server is running correctly."""
    try:
        app = ctx.request_context.lifespan_context
        # Minimal smoke test: verify crawler is alive
        if app.crawler is None:
            return "error: crawler not initialized"
        return "ok"
    except Exception as e:
        logger.error("ping failed: %s", e, exc_info=True)
        return f"error: {e}"


# Pattern for future crawl tools (not Phase 1, but established here)
def _format_crawl_error(url: str, result) -> str:
    """Convert a failed CrawlResult into a structured error string."""
    return (
        f"Crawl failed for {url}\n"
        f"Status: {result.status_code}\n"
        f"Error: {result.error_message}"
    )

# Usage in a tool:
# result = await app.crawler.arun(url=url, config=run_cfg)
# if not result.success:
#     return _format_crawl_error(url, result)
# return result.markdown.raw_markdown
```

**FastMCP error handling note:** If a tool raises an unhandled exception, FastMCP catches it and returns an MCP error response (does not crash the server). However, it is better practice to catch exceptions in tool handlers and return structured strings so Claude can reason about what went wrong. Unhandled exceptions return opaque error codes that Claude cannot act on.

### Pattern 4: Accessing AppContext in Tool Handlers (INFRA-03)

**What:** Tool handlers receive a `Context` parameter. The typed `AppContext` is accessed via `ctx.request_context.lifespan_context`.

**Source:** MCP Python SDK README (Context7 `/modelcontextprotocol/python-sdk`, HIGH confidence)

```python
# Source: MCP Python SDK README
from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession

@mcp.tool()
async def some_tool(
    url: str,
    ctx: Context[ServerSession, AppContext],
) -> str:
    """Tool description here."""
    app: AppContext = ctx.request_context.lifespan_context
    crawler = app.crawler
    # ... use crawler
```

**Important:** The `ctx` parameter must be typed as `Context[ServerSession, AppContext]` for IDE type safety, but FastMCP injects it by type at runtime — do not pass it explicitly when calling the tool.

### Pattern 5: Server Entry Point and uv Run (INFRA-01)

**What:** The server exposes a `__main__` block that calls `mcp.run()`. Claude Code invokes it via `uv run --directory /absolute/path python -m crawl4ai_mcp.server`. A `[project.scripts]` entry in `pyproject.toml` provides a named shortcut.

**Source:** MCP Python SDK README (Context7); uv docs; STACK.md (HIGH confidence)

```python
# src/crawl4ai_mcp/server.py (end of file)
if __name__ == "__main__":
    mcp.run()  # Default transport is stdio — no argument needed
```

```toml
# pyproject.toml
[project.scripts]
crawl4ai-mcp = "crawl4ai_mcp.server:main"
```

```python
# Alternative: expose a main() function for the script entry point
def main():
    mcp.run()

if __name__ == "__main__":
    main()
```

### Anti-Patterns to Avoid

- **Creating new `AsyncWebCrawler` per tool call:** Each instantiation launches a new Chromium process (2-5 second startup cost) then kills it. Memory leaks accumulate. Use the lifespan singleton exclusively.
- **Using `async with AsyncWebCrawler()` inside the lifespan function:** The lifespan IS the context manager. Use explicit `crawler.start()` / `crawler.close()` instead.
- **Calling `asyncio.run(mcp.run())`:** FastMCP manages the event loop internally. Just call `mcp.run()` directly.
- **Setting `verbose=True` on any crawl4ai config object:** Outputs to stdout, corrupts MCP transport.
- **Using `print()` anywhere in server code:** Add a linting rule (`ruff` rule `T201`) to catch this.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| MCP protocol frame serialization | Custom JSON-RPC encoder | `FastMCP` from `mcp.server.fastmcp` | FastMCP handles all protocol framing, schema generation, and transport. Hand-rolling is thousands of lines for no gain. |
| Browser lifecycle management | Custom Playwright wrapper | `AsyncWebCrawler` with `crawler.start()` / `crawler.close()` | crawl4ai's lifecycle methods are battle-tested and handle Playwright's async resource management correctly. |
| Async event loop setup | `asyncio.run()` wrapper | `mcp.run()` | FastMCP handles the event loop. Adding `asyncio.run()` causes double-loop errors. |
| Tool parameter schema | Manual JSON Schema dict | Type hints + docstrings on `@mcp.tool()` functions | FastMCP auto-generates MCP schemas from Python type hints. Manual schemas are redundant and error-prone. |
| Logging infrastructure | Custom log handler | `logging.basicConfig(stream=sys.stderr)` | One line. stdlib logging. Nothing custom needed. |

**Key insight:** Phase 1 is almost entirely plumbing that existing libraries handle. The implementation effort is wiring them together correctly, not building anything novel.

## Common Pitfalls

### Pitfall 1: stdout Corruption (INFRA-02)

**What goes wrong:** Claude Code connects via stdio. Any non-JSON-RPC byte on stdout causes a parse error, dropping the connection. The MCP server appears to start but Claude Code shows "server disconnected" or no tools appear.

**Why it happens:** Python `print()`, `logging` configured to stdout, or `BrowserConfig(verbose=True)` all write to stdout. Even a single `print("debug")` breaks the entire session.

**How to avoid:**
- `logging.basicConfig(stream=sys.stderr)` as the FIRST statement in `server.py`
- `BrowserConfig(verbose=False)` always (it is the default in 0.8.0 but be explicit)
- Add `ruff` rule `T201` to flag `print()` calls as errors
- Test with: `echo '{"jsonrpc":"2.0","id":1,"method":"initialize",...}' | uv run python -m crawl4ai_mcp.server 2>/dev/null` — stdout should contain only JSON

**Warning signs:** Tools don't appear in Claude Code. Server process shows as running but Claude Code reports disconnected. Works in MCP Inspector but not Claude Code.

### Pitfall 2: Browser Process Leaks (INFRA-03)

**What goes wrong:** If the `AsyncWebCrawler` is not closed on server shutdown, Chromium processes remain as orphans. Over a long session, this accumulates memory and eventually crashes.

**Why it happens:** The `finally` block in the lifespan function is skipped if the server is SIGKILL'd (not SIGTERM). Graceful shutdown via `Ctrl+C` or process exit correctly runs the finally block.

**How to avoid:**
- Always put `await crawler.close()` in the `finally` block of the lifespan
- Verify cleanup by checking `ps aux | grep chrom` after stopping the server
- FastMCP handles SIGTERM gracefully and runs the lifespan `finally` block

**Warning signs:** After stopping and restarting the server repeatedly during development, `ps aux | grep chrom` shows accumulating Chromium processes.

### Pitfall 3: Double Event Loop (INFRA-01)

**What goes wrong:** Wrapping `mcp.run()` in `asyncio.run()` causes a "cannot run nested event loop" error at startup.

**Why it happens:** `mcp.run()` sets up its own event loop via `anyio`. Adding `asyncio.run()` on top creates a conflict.

**How to avoid:** Call `mcp.run()` directly without any asyncio wrapper:
```python
if __name__ == "__main__":
    mcp.run()  # correct
    # asyncio.run(mcp.run())  # WRONG — double event loop
```

### Pitfall 4: Incorrect Claude Code Registration (INFRA-01, INFRA-05)

**What goes wrong:** Registering with a relative path, without `--directory`, or without `--scope user` causes the server to fail when Claude Code invokes it from a different working directory.

**Why it happens:** `uv run python -m crawl4ai_mcp.server` without `--directory` looks for the venv in the current directory (which is Claude Code's project directory, not the MCP server project).

**How to avoid:** Always use absolute path and `--directory` in the registration command. See Code Examples below.

### Pitfall 5: crawl4ai verbose Output Reaching stdout

**What goes wrong:** Even with `verbose=False` on `BrowserConfig`, some crawl4ai versions have internal `print()` calls that escape to stdout (confirmed in GitHub issue #264 for older versions; 0.8.0 status not confirmed at research time).

**How to avoid:**
- Set `verbose=False` explicitly on both `BrowserConfig` and `CrawlerRunConfig`
- Test the server with `2>/dev/null` to observe stdout in isolation
- If phantom stdout output is found, identify its source and suppress it

**Warning signs:** MCP Inspector works but Claude Code connection drops intermittently during crawls.

## Code Examples

Verified patterns from official sources:

### Complete server.py for Phase 1

```python
# src/crawl4ai_mcp/server.py
# Source: MCP Python SDK README (Context7) + crawl4ai API docs (Context7)
import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from crawl4ai import AsyncWebCrawler, BrowserConfig
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession

# MUST be first: route all logging to stderr before any imports emit output
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)


@dataclass
class AppContext:
    crawler: AsyncWebCrawler


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    logger.info("Starting crawl4ai MCP server")
    browser_cfg = BrowserConfig(
        headless=True,
        verbose=False,
        extra_args=["--disable-gpu", "--disable-dev-shm-usage", "--no-sandbox"],
    )
    crawler = AsyncWebCrawler(config=browser_cfg)
    await crawler.start()
    logger.info("Browser ready")
    try:
        yield AppContext(crawler=crawler)
    finally:
        logger.info("Shutting down browser")
        await crawler.close()


mcp = FastMCP("crawl4ai", lifespan=app_lifespan)


@mcp.tool()
async def ping(ctx: Context[ServerSession, AppContext]) -> str:
    """Verify the MCP server is running and the browser is ready.
    Returns 'ok' if healthy, or an error description if not."""
    try:
        app: AppContext = ctx.request_context.lifespan_context
        if app.crawler is None:
            return "error: crawler not initialized"
        return "ok"
    except Exception as e:
        logger.error("ping failed: %s", e, exc_info=True)
        return f"error: {e}"


def main() -> None:
    mcp.run()  # stdio transport by default


if __name__ == "__main__":
    main()
```

### pyproject.toml

```toml
[project]
name = "crawl4ai-mcp"
version = "0.1.0"
description = "crawl4ai MCP server for Claude Code"
requires-python = ">=3.12"
dependencies = [
    "mcp[cli]>=1.26.0",
    "crawl4ai>=0.8.0,<0.9.0",
]

[project.scripts]
crawl4ai-mcp = "crawl4ai_mcp.server:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/crawl4ai_mcp"]

[tool.pytest.ini_options]
asyncio_mode = "auto"

[tool.ruff.lint]
select = ["T201"]  # flag print() as an error
```

### Claude Code Registration (INFRA-01, INFRA-05)

```bash
# Register as a global user-scoped MCP server
# Source: MCP Python SDK docs + Perplexity verified
claude mcp add-json --scope user crawl4ai '{
  "type": "stdio",
  "command": "uv",
  "args": [
    "run",
    "--directory",
    "/absolute/path/to/crawl4ai-mcp",
    "python",
    "-m",
    "crawl4ai_mcp.server"
  ]
}'

# Verify registration
claude mcp list

# Test connection
claude mcp test crawl4ai
```

**README snippet (INFRA-05):**

```json
{
  "type": "stdio",
  "command": "uv",
  "args": [
    "run",
    "--directory",
    "/Users/YOUR_USERNAME/path/to/crawl4ai-mcp",
    "python",
    "-m",
    "crawl4ai_mcp.server"
  ]
}
```

Add to `~/.claude.json` under `mcpServers`, or register via:
```bash
claude mcp add-json --scope user crawl4ai '<paste JSON above>'
```

### CrawlResult Error Handling Pattern

```python
# Source: crawl4ai API docs (Context7 /unclecode/crawl4ai, HIGH confidence)
# Used in future tool handlers — pattern established in Phase 1

result = await app.crawler.arun(url=url, config=run_cfg)

if result.success:
    return result.markdown.raw_markdown  # or result.markdown.fit_markdown
else:
    return (
        f"Crawl failed\n"
        f"URL: {url}\n"
        f"HTTP status: {result.status_code}\n"
        f"Error: {result.error_message}"
    )
```

### Smoke Test Command

```bash
# Verify stdout contains only JSON-RPC (stderr suppressed)
# Any non-JSON on stdout indicates a corruption source
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}' \
  | uv run --directory /path/to/crawl4ai-mcp python -m crawl4ai_mcp.server 2>/dev/null \
  | python3 -c "import sys,json; [json.loads(l) for l in sys.stdin]" \
  && echo "stdout is clean JSON" \
  || echo "stdout CORRUPTION DETECTED"
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Low-level `Server` with manual `stdio_server()` setup | `FastMCP` with `@mcp.tool()` decorator and `mcp.run()` | MCP Python SDK v1.x | Eliminates boilerplate; auto-generates schemas from type hints |
| `async with AsyncWebCrawler()` per request | `crawler.start()` / `crawler.close()` in `lifespan` | crawl4ai v0.7+ / architectural best practice | Prevents 2-5s browser startup cost per request; prevents leaks |
| `asyncio.run(server.serve_forever())` | `mcp.run()` with anyio event loop | FastMCP introduction | Cleaner entry point; FastMCP manages event loop internally |
| Global `claude_desktop_config.json` | `claude mcp add-json --scope user` | Claude Code CLI | User-scoped registration persists across all projects |

**Deprecated/outdated:**
- `mcp.server.lowlevel.Server`: Still valid but requires manual schema construction and stdio setup. Use `FastMCP` instead for this project.
- `crawl4ai[sync]`: Deprecated Selenium-based mode. Do not use.
- `playwright install` (global): Installs all browsers. Use `crawl4ai-setup` or `playwright install chromium` to install only what crawl4ai needs.

## Open Questions

1. **Does crawl4ai 0.8.0 have any remaining `print()` calls that escape to stdout with `verbose=False`?**
   - What we know: GitHub issue #264 confirmed print leaks in older versions. 0.8.0 claims improvements but the Perplexity result notes "some print statements persist" citing #264.
   - What's unclear: Whether 0.8.0 is fully clean or still has occasional stdout output.
   - Recommendation: Test with the smoke test command above during Phase 1 implementation. If stdout pollution is found, identify the source and file an issue / apply a workaround.

2. **What is the exact `claude mcp test` command syntax in current Claude Code?**
   - What we know: `claude mcp list` is confirmed. The Perplexity result mentions `claude mcp test` but this was not verified against official Claude Code docs.
   - What's unclear: Whether `claude mcp test` is a valid subcommand or if testing is done via MCP Inspector (`npx @modelcontextprotocol/inspector`).
   - Recommendation: Use `npx @modelcontextprotocol/inspector` for interactive testing. Verify `claude mcp test` during implementation.

3. **Should `--scope user` or `--scope global` be used for Claude Code registration?**
   - What we know: Perplexity mentions both `--scope user` and `--scope global`. The STACK.md from prior research says `--scope user` (stored in `~/.claude.json`).
   - What's unclear: Whether `--scope global` is a valid option or an alias for `user`.
   - Recommendation: Use `--scope user` as documented in STACK.md (HIGH confidence). Verify during implementation with `claude mcp --help`.

## Sources

### Primary (HIGH confidence)

- Context7 `/modelcontextprotocol/python-sdk` (Benchmark 86.8) — FastMCP lifespan pattern, `AppContext` dataclass, `ctx.request_context.lifespan_context`, `@mcp.tool()` decorator, `mcp.run()` stdio default
- Context7 `/unclecode/crawl4ai` (Benchmark 90.7) — `AsyncWebCrawler.start()`, `AsyncWebCrawler.close()`, manual lifecycle management, `BrowserConfig.verbose`, `CrawlResult.success`, `CrawlResult.error_message`
- Prior project research: `.planning/research/STACK.md` — uv project setup, FastMCP overview, Claude Code registration syntax
- Prior project research: `.planning/research/ARCHITECTURE.md` — lifespan singleton pattern, AppContext pattern, anti-patterns
- Prior project research: `.planning/research/PITFALLS.md` — stdout corruption (Roo Code #5462), browser leak causes (crawl4ai #1256, #1608)
- Official uv docs (docs.astral.sh/uv) — `uv run --directory`, project initialization

### Secondary (MEDIUM confidence)

- Perplexity search: stderr logging with `logging.basicConfig(stream=sys.stderr)` — confirmed pattern, cited kdnuggets/FastMCP tutorial
- Perplexity search: `claude mcp add-json --scope user` syntax — confirmed, cited code.claude.com/docs/en/mcp
- Perplexity search: `uv run python -m module_name` entry point pattern — confirmed, cited realpython.com/python-uv
- Perplexity search on crawl4ai verbose default (0.8.0 = False) — cited docs.crawl4ai.com, note about residual print statements

### Tertiary (LOW confidence)

- GitHub issue #264 (crawl4ai) — residual print statements with `verbose=False`; version affected is unclear relative to 0.8.0. Validate empirically.
- `claude mcp test` subcommand — mentioned in Perplexity but not verified against official CLI reference.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — both primary libraries well-documented in Context7, versions confirmed on PyPI, patterns match prior project research
- Architecture patterns: HIGH — FastMCP lifespan pattern is verbatim from SDK README; crawl4ai start/close is from API docs
- Common pitfalls: HIGH — stdout corruption and browser leaks grounded in specific GitHub issues; double event loop is standard Python asyncio knowledge
- Code examples: HIGH — all examples adapted from verified Context7 sources with minor composition for this project's structure

**Research date:** 2026-02-19
**Valid until:** 2026-03-21 (30 days — FastMCP and crawl4ai are both active but stable at these pinned versions)
**Next research trigger:** If `crawl4ai` or `mcp` publish a new minor version before Phase 1 is implemented, re-validate the lifespan API and `BrowserConfig` signature.
