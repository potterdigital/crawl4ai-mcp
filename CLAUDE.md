# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A local Python MCP server that wraps [crawl4ai](https://docs.crawl4ai.com) and exposes web crawling capabilities as MCP tools for Claude Code. Runs as a global `stdio` MCP server registered with Claude Code — not distributed, local-only.

## Development Commands

```bash
# Install dependencies (uv manages the virtualenv)
uv sync

# Install Playwright browser (Chromium — required by crawl4ai)
uv run crawl4ai-setup

# Run the server directly (for debugging)
uv run python -m crawl4ai_mcp.server

# Lint (checks for print() calls that would corrupt stdio — ruff T201 rule)
uv run ruff check src/

# Run tests
uv run pytest

# Diagnose crawl4ai / Playwright health
uv run crawl4ai-doctor
```

## Architecture

```
Claude Code (MCP client, stdio)
    └── FastMCP Server (server.py)
            ├── Tools (@mcp.tool() decorated async functions)
            ├── Profile Manager (loads profiles/*.yaml at startup)
            ├── Config Builder (profile + overrides -> BrowserConfig + CrawlerRunConfig)
            └── AsyncWebCrawler singleton (created in lifespan, shared across all tool calls)
                    └── Playwright / Chromium (headless)
```

**Entry point**: `src/crawl4ai_mcp/server.py` — holds the `FastMCP` instance, `app_lifespan`, `AppContext`, and all registered tools (for now). As tools are added in later phases they will move to `tools/` submodules.

**Planned module structure** (phases 2+):
```
src/crawl4ai_mcp/
├── server.py           # FastMCP instance, lifespan, tool imports
├── crawler.py          # AsyncWebCrawler lifecycle (phase 2)
├── config_builder.py   # Profile + override -> crawl4ai config objects (phase 2)
├── profiles.py         # ProfileManager: load/validate/merge YAML (phase 3)
├── tools/
│   ├── crawl.py        # crawl_url (phase 2)
│   ├── extract.py      # extract_structured, extract_css (phase 4)
│   ├── batch.py        # crawl_many, deep_crawl, crawl_sitemap (phase 5)
│   └── admin.py        # check_update, list_profiles (phase 7)
└── profiles/           # YAML profile files (phase 3)
    ├── default.yaml
    ├── fast.yaml
    ├── js_heavy.yaml
    └── stealth.yaml
```

## Critical Constraints

**stdout must stay clean** — the MCP `stdio` transport uses stdout exclusively for JSON-RPC frames. Any stray output (from `print()`, `verbose=True`, or a library) immediately corrupts the protocol and disconnects Claude Code. All logging must go to `stderr`. This is why:
- `logging.basicConfig(stream=sys.stderr)` is the very first line in `server.py` (before all imports that might log)
- `BrowserConfig(verbose=False)` is hardcoded
- ruff's `T201` rule catches any `print()` calls

**Never create `AsyncWebCrawler` per tool call** — Chromium startup takes 2–5 seconds. The singleton created in `app_lifespan` must be reused. Access it via `ctx.request_context.lifespan_context` (typed as `AppContext`).

**Never upgrade packages in-process** — the `check_update` tool reports available updates; it never runs `pip install`. The actual upgrade must happen offline via `scripts/update.sh` (to be created in phase 7), followed by a server restart.

**Tool error handling** — tools return structured error strings rather than raising exceptions. This lets Claude reason about failures and decide how to proceed. See `_format_crawl_error()` for the established pattern.

## Tool Context Pattern

All tool functions access the shared crawler via FastMCP's `Context`:

```python
from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession

@mcp.tool()
async def my_tool(url: str, ctx: Context[ServerSession, AppContext]) -> str:
    app: AppContext = ctx.request_context.lifespan_context
    result = await app.crawler.arun(url=url, config=run_cfg)
    ...
```

## Profile Merge Order (phase 3+)

Config is merged in this precedence order (later overrides earlier):
`default profile ← named profile ← per-call overrides`

## Phase Status

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Foundation: server scaffold, browser lifecycle, `ping` tool, README | **Complete** |
| 2 | Core Crawl: `crawl_url` with full param control, markdown output | **Complete** |
| 3 | Profile System: YAML profiles + per-call override merging | **Complete** |
| 4 | Extraction: `extract_structured` (LLM) and `extract_css` (deterministic) | Not started |
| 5 | Multi-Page: `crawl_many`, `deep_crawl`, `crawl_sitemap` | Not started |
| 6 | Authentication: cookie injection, named browser sessions | Not started |
| 7 | Update Management: `check_update` tool, startup warning, `scripts/update.sh` | Not started |

Phases 6 and 7 depend only on Phase 2 and Phase 1 respectively — they can start independently of 3–5.

## MCP Registration (Claude Code)

```bash
claude mcp add-json --scope user crawl4ai '{
  "type": "stdio",
  "command": "uv",
  "args": ["run", "--directory", "/Users/brianpotter/ai_tools/crawl4ai_mcp", "python", "-m", "crawl4ai_mcp.server"]
}'
```

The `--directory` flag is required — without it, `uv run` looks for the virtualenv in Claude Code's working directory.

## Debugging

To see server logs while running:
```bash
uv run python -m crawl4ai_mcp.server 2>&1 1>/dev/null
```
This shows stderr (logs) while discarding stdout (MCP frames).
