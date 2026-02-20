# Architecture Research

**Domain:** Python MCP server wrapping crawl4ai for Claude Code
**Researched:** 2026-02-19
**Confidence:** HIGH

## Standard Architecture

### System Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                     Claude Code (MCP Client)                        │
│                        stdio transport                              │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │                    FastMCP Server Layer                        │  │
│  │   @mcp.tool() decorators, lifespan, Context injection         │  │
│  ├───────────────────────────────────────────────────────────────┤  │
│  │                                                               │  │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐     │  │
│  │  │  crawl   │  │ extract  │  │  batch   │  │  admin   │     │  │
│  │  │  tools   │  │  tools   │  │  tools   │  │  tools   │     │  │
│  │  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘     │  │
│  │       │              │             │              │           │  │
│  ├───────┴──────────────┴─────────────┴──────────────┴───────────┤  │
│  │                    Profile Manager                             │  │
│  │        loads YAML profiles, merges per-call overrides          │  │
│  ├───────────────────────────────────────────────────────────────┤  │
│  │                  Crawler Manager (Lifespan)                    │  │
│  │     AsyncWebCrawler singleton, browser lifecycle               │  │
│  ├───────────────────────────────────────────────────────────────┤  │
│  │                   Config Builder                               │  │
│  │    profile + overrides -> BrowserConfig + CrawlerRunConfig     │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│                        crawl4ai Library                              │
│   AsyncWebCrawler | BrowserConfig | CrawlerRunConfig | Strategies   │
├─────────────────────────────────────────────────────────────────────┤
│                     Playwright (Browser Engine)                      │
│                    Chromium headless instance                        │
└─────────────────────────────────────────────────────────────────────┘
```

### Component Responsibilities

| Component | Responsibility | Typical Implementation |
|-----------|----------------|------------------------|
| **FastMCP Server** | Protocol handling, tool registration, stdio transport, lifespan management | Single `FastMCP("crawl4ai")` instance with `lifespan` async context manager |
| **Tool Modules** | Individual MCP tool functions organized by concern | Python modules with `@mcp.tool()` decorated async functions |
| **Profile Manager** | Load, validate, and merge YAML crawl profiles | Reads `profiles/*.yaml` at startup, caches in memory, merges with per-call overrides |
| **Crawler Manager** | Owns the `AsyncWebCrawler` singleton, manages browser lifecycle | Created in lifespan `__aenter__`, stored in `AppContext`, closed in `__aexit__` |
| **Config Builder** | Translates profile + overrides into crawl4ai config objects | Pure functions: `dict -> BrowserConfig`, `dict -> CrawlerRunConfig` |
| **Admin Tools** | Version checking, update triggering, profile listing | Tools that inspect `importlib.metadata`, query PyPI, and invoke pip |

## Recommended Project Structure

```
crawl4ai_mcp/
├── server.py               # Entry point: FastMCP instance, lifespan, tool imports
├── crawler.py              # CrawlerManager: AsyncWebCrawler lifecycle
├── config_builder.py       # BrowserConfig/CrawlerRunConfig factory functions
├── profiles.py             # ProfileManager: load, validate, merge YAML profiles
├── tools/
│   ├── __init__.py         # Re-exports tool registration functions
│   ├── crawl.py            # crawl_url, crawl_markdown tools
│   ├── extract.py          # extract_structured, extract_css tools
│   ├── batch.py            # crawl_many, deep_crawl tools
│   └── admin.py            # check_update, list_profiles, server_info tools
├── profiles/
│   ├── default.yaml        # Balanced defaults
│   ├── fast.yaml           # Speed-optimized (text_mode, light_mode)
│   ├── js_heavy.yaml       # Full JS rendering, longer waits
│   └── stealth.yaml        # Random UA, realistic viewport, slow delays
├── scripts/
│   └── update.sh           # Safe offline update script (pip upgrade + playwright)
├── pyproject.toml          # Dependencies: mcp, crawl4ai, pyyaml
└── README.md               # Setup and Claude Code MCP config instructions
```

### Structure Rationale

- **`server.py` as entry point:** Single file Claude Code points to. Contains the `FastMCP` instance, lifespan function, and imports all tool modules. Keeps the "what does this server expose?" question answerable in one file.
- **`tools/` directory:** Groups tools by domain (crawl, extract, batch, admin). Each module registers tools on the shared `mcp` instance imported from `server.py`. This keeps individual files focused and testable.
- **`profiles/` directory:** YAML files that non-developers can edit. Each profile is a partial dict of `BrowserConfig` and `CrawlerRunConfig` parameters. No code changes needed to add a new profile.
- **`crawler.py` separate from `server.py`:** The crawler lifecycle is complex enough (start, reuse, close, error recovery) to warrant its own module. Keeps server.py clean.
- **`config_builder.py` as pure functions:** Translation from dicts to crawl4ai config objects is stateless and testable. No side effects.
- **`scripts/update.sh`:** Update is deliberately a shell script, not a Python tool that runs in-process. Updating pip packages while the server is running is unsafe. The MCP tool reports what to do; the script does it offline.

## Architectural Patterns

### Pattern 1: Lifespan-Managed Singleton Crawler

**What:** Use FastMCP's `lifespan` async context manager to create a single `AsyncWebCrawler` instance at server startup and close it at shutdown. All tool calls share this instance via typed `Context`.

**When to use:** Always. This is the core pattern for the entire server.

**Trade-offs:** Browser stays warm (fast subsequent crawls) but consumes memory. Single browser instance means one set of cookies/headers at the browser level, though per-crawl overrides are possible via `CrawlerRunConfig`.

**Example:**
```python
from dataclasses import dataclass
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from crawl4ai import AsyncWebCrawler, BrowserConfig
from mcp.server.fastmcp import FastMCP, Context
from mcp.server.session import ServerSession

@dataclass
class AppContext:
    crawler: AsyncWebCrawler
    profiles: dict  # loaded profile configs

@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    browser_cfg = BrowserConfig(headless=True, verbose=False)
    crawler = AsyncWebCrawler(config=browser_cfg)
    await crawler.start()
    profiles = load_profiles()  # from profiles/*.yaml
    try:
        yield AppContext(crawler=crawler, profiles=profiles)
    finally:
        await crawler.close()

mcp = FastMCP("crawl4ai", lifespan=app_lifespan)
```

### Pattern 2: Profile + Override Merging

**What:** Every crawl tool accepts an optional `profile` name and optional per-call overrides. The Config Builder merges: `default profile <- named profile <- per-call overrides` to produce final `BrowserConfig` and `CrawlerRunConfig` objects.

**When to use:** Every tool that performs a crawl.

**Trade-offs:** Adds a layer of indirection, but dramatically simplifies Claude's tool calls (just pass `profile="stealth"` instead of 15 parameters). Profiles are editable without code changes.

**Example:**
```python
@mcp.tool()
async def crawl_url(
    url: str,
    profile: str = "default",
    wait_for: str | None = None,
    css_selector: str | None = None,
    headless: bool | None = None,
    ctx: Context[ServerSession, AppContext] = None,
) -> str:
    app = ctx.request_context.lifespan_context

    # Merge: default <- profile <- explicit overrides
    overrides = {k: v for k, v in {
        "wait_for": wait_for,
        "css_selector": css_selector,
    }.items() if v is not None}

    run_cfg = build_run_config(app.profiles, profile, overrides)
    result = await app.crawler.arun(url=url, config=run_cfg)
    return result.markdown_v2.raw_markdown if result.success else f"Error: {result.error_message}"
```

### Pattern 3: Offline Update Script (Not In-Process)

**What:** The MCP `check_update` tool only *reports* whether an update is available and returns the shell command to run. It never performs the update itself. A separate `scripts/update.sh` handles `pip install --upgrade crawl4ai` and `playwright install` outside the running server.

**When to use:** Always for updates. Never pip-install inside a running MCP server process.

**Trade-offs:** Requires manual (or Claude-initiated) server restart after update. But this is far safer than corrupting a running Python process by upgrading its own dependencies mid-execution.

**Example:**
```python
@mcp.tool()
async def check_update(ctx: Context[ServerSession, AppContext]) -> str:
    """Check if a crawl4ai update is available."""
    import importlib.metadata
    current = importlib.metadata.version("crawl4ai")

    # Query PyPI for latest
    import httpx
    async with httpx.AsyncClient() as client:
        resp = await client.get("https://pypi.org/pypi/crawl4ai/json")
        latest = resp.json()["info"]["version"]

    if latest == current:
        return f"crawl4ai is up to date: {current}"

    return (
        f"Update available: {current} -> {latest}\n"
        f"To update, stop the server and run:\n"
        f"  ./scripts/update.sh"
    )
```

## Data Flow

### Single-URL Crawl Flow

```
Claude Code tool call: crawl_url(url="...", profile="stealth", css_selector=".content")
    |
    v
FastMCP deserializes params, injects Context with AppContext
    |
    v
crawl_url() tool function
    |
    ├── ProfileManager: load "stealth" profile from cached YAML
    |       |
    |       v
    ├── ConfigBuilder: merge profile + {css_selector: ".content"}
    |       |
    |       v
    |   CrawlerRunConfig(css_selector=".content", user_agent="random", ...)
    |
    v
AppContext.crawler.arun(url=url, config=run_cfg)
    |
    v
crawl4ai: Playwright navigates, waits, extracts content
    |
    v
CrawlResult (success, markdown, extracted_content, links, metadata)
    |
    v
Tool returns markdown string to Claude Code via stdio
```

### Structured Extraction Flow

```
Claude Code: extract_structured(url="...", schema={...}, instruction="...", profile="default")
    |
    v
Tool builds LLMExtractionStrategy from schema + instruction params
    |
    v
ConfigBuilder creates CrawlerRunConfig with extraction_strategy set
    |
    v
AppContext.crawler.arun(url=url, config=run_cfg)
    |
    v
crawl4ai: crawl page -> chunk content -> send to LLM -> parse response
    |
    v
CrawlResult.extracted_content = JSON string
    |
    v
Tool returns parsed JSON to Claude Code
```

### Batch Crawl Flow

```
Claude Code: crawl_many(urls=["a.com", "b.com", ...], profile="fast")
    |
    v
Tool builds configs, calls crawler.arun_many(urls=urls, config=run_cfg)
    |
    v
crawl4ai: MemoryAdaptiveDispatcher manages concurrency
    |
    ├── arun("a.com") ──> CrawlResult
    ├── arun("b.com") ──> CrawlResult    (parallel, memory-adaptive)
    └── arun("c.com") ──> CrawlResult
    |
    v
Tool aggregates results, returns combined markdown/JSON
```

### Update Check Flow

```
Claude Code: check_update()
    |
    v
Tool reads importlib.metadata.version("crawl4ai")  -> "0.7.7"
    |
    v
Tool queries https://pypi.org/pypi/crawl4ai/json   -> "0.8.0"
    |
    v
Tool returns: "Update available: 0.7.7 -> 0.8.0\nRun: ./scripts/update.sh"
    |
    v
Claude Code shows user the message. User stops server, runs script, restarts.
```

## Scaling Considerations

| Scale | Architecture Adjustments |
|-------|--------------------------|
| Single user, local (target) | Single AsyncWebCrawler instance, one Chromium process. No scaling needed. This is the design target. |
| Heavy crawl sessions (10+ concurrent URLs) | `arun_many()` with `MemoryAdaptiveDispatcher` handles this automatically. May need to increase `viewport_height` or use `text_mode` to reduce memory per page. |
| Very large batch jobs (100+ URLs) | Stream results via `stream=True` in `CrawlerRunConfig`. Return partial results as they complete rather than waiting for all. Consider `light_mode=True` in BrowserConfig. |

### Scaling Priorities

1. **First bottleneck: Memory.** Each Chromium tab consumes 50-200MB. For large batches, use `text_mode=True` and `light_mode=True` profiles to reduce footprint. `MemoryAdaptiveDispatcher` handles this automatically but the "fast" profile should pre-configure these flags.
2. **Second bottleneck: Rate limiting.** Sites will block aggressive crawling. The "stealth" profile should include realistic delays, random user agents, and viewport sizes. `arun_many` has built-in rate limiting and backoff.

## Anti-Patterns

### Anti-Pattern 1: Creating a New AsyncWebCrawler Per Tool Call

**What people do:** Instantiate `AsyncWebCrawler()` inside each tool function, use it as a context manager, let it close.

**Why it's wrong:** Each instantiation launches a new Chromium process (2-5 second startup), then kills it. For sequential tool calls (common in Claude Code sessions), this means constant browser churn. Playwright browser startup is the single most expensive operation in crawl4ai.

**Do this instead:** Use the lifespan pattern. Create one `AsyncWebCrawler` at server startup via `lifespan`, store in `AppContext`, share across all tool calls. The browser stays warm for the entire session.

### Anti-Pattern 2: In-Process pip Upgrade

**What people do:** Call `subprocess.run(["pip", "install", "--upgrade", "crawl4ai"])` from within an MCP tool handler while the server is running.

**Why it's wrong:** Upgrading a package that the running Python process has already imported can cause: (a) old cached bytecode (.pyc) conflicts, (b) partially-loaded new modules mixed with old ones, (c) Playwright version mismatches if the browser binary isn't updated, (d) corrupted server state. The server would need a full restart anyway.

**Do this instead:** The `check_update` tool reports the available update and the command to run. The actual update happens offline via `scripts/update.sh` which: (1) `pip install --upgrade crawl4ai`, (2) `playwright install chromium`, (3) verifies the installation. Claude Code restarts the MCP server automatically when the process exits.

### Anti-Pattern 3: Passing All crawl4ai Parameters as Tool Arguments

**What people do:** Expose every single `BrowserConfig` and `CrawlerRunConfig` parameter as a tool argument, resulting in tools with 20+ parameters.

**Why it's wrong:** LLMs struggle with tools that have many parameters. Most parameters have sensible defaults that rarely change. Claude has to guess at values for parameters it does not understand, leading to failed crawls.

**Do this instead:** Use the profile system. Tools accept a `profile` name and a small number of common override parameters (`url`, `css_selector`, `wait_for`, `timeout`). The profile handles the other 15+ parameters. Advanced users edit the YAML profiles directly.

### Anti-Pattern 4: Blocking Sync Calls in Async Tool Handlers

**What people do:** Use `requests.get()` or synchronous file I/O inside `@mcp.tool()` async functions.

**Why it's wrong:** Blocks the asyncio event loop, preventing concurrent tool execution. The MCP server becomes single-threaded in practice.

**Do this instead:** Use `httpx.AsyncClient` for HTTP, `aiofiles` for file I/O, and `asyncio.to_thread()` for any unavoidable sync operations.

## Integration Points

### External Services

| Service | Integration Pattern | Notes |
|---------|---------------------|-------|
| PyPI API | `httpx.AsyncClient` GET to `pypi.org/pypi/crawl4ai/json` | Used only by `check_update` tool. No auth needed. Cache response for session. |
| LLM Providers (OpenAI, etc.) | Via crawl4ai's `LLMExtractionStrategy` + `LLMConfig` | crawl4ai handles the LLM API calls internally. Server just passes config through. API keys come from environment variables. |
| Playwright CDN | Browser binary downloads during `playwright install` | Only during `scripts/update.sh`, never at runtime. |

### Internal Boundaries

| Boundary | Communication | Notes |
|----------|---------------|-------|
| `server.py` <-> `tools/*` | Python imports; tools register on shared `mcp` instance | Tools import `mcp` from server module and decorate functions. |
| `tools/*` <-> `crawler.py` | Via `AppContext` injected through `Context` | Tools never create crawlers directly; always go through the lifespan-managed instance. |
| `tools/*` <-> `profiles.py` | Via `AppContext.profiles` dict | Profiles loaded once at startup, cached in memory. Profile changes require server restart. |
| `tools/*` <-> `config_builder.py` | Pure function calls | Stateless translation: `(profile_dict, overrides) -> crawl4ai config objects`. |
| `admin tools` <-> `PyPI` | Async HTTP (httpx) | Only the admin module touches external APIs for version checks. |

## Build Order

The components have clear dependencies that dictate implementation order:

```
Phase 1: Foundation (no crawl4ai yet)
  server.py (FastMCP + lifespan skeleton)
  └── Can test: server starts, stdio works, lifespan runs

Phase 2: Crawler Core (minimum viable crawl)
  crawler.py (AsyncWebCrawler in lifespan)
  config_builder.py (basic BrowserConfig/CrawlerRunConfig)
  tools/crawl.py (crawl_url tool)
  └── Can test: crawl a URL from Claude Code, get markdown back

Phase 3: Configuration (profiles before more tools)
  profiles.py (YAML loading, merging)
  profiles/*.yaml (default, fast, js_heavy, stealth)
  └── Can test: crawl_url with profile="stealth" works differently

Phase 4: Extraction Tools (builds on crawler + config)
  tools/extract.py (structured extraction with LLM/CSS strategies)
  └── Can test: extract JSON from a page with a Pydantic schema

Phase 5: Batch Tools (builds on crawler + config + extraction)
  tools/batch.py (crawl_many, deep_crawl)
  └── Can test: crawl 5 URLs in parallel, get combined results

Phase 6: Admin & Polish
  tools/admin.py (check_update, list_profiles, server_info)
  scripts/update.sh
  └── Can test: check_update reports version, update.sh works offline
```

**Key dependency:** Phases 2-3 must be solid before 4-5. The crawler singleton and profile system are used by every subsequent tool. Getting the lifespan and config merging right early avoids rework.

## Sources

- MCP Python SDK official documentation and README (Context7: `/modelcontextprotocol/python-sdk`) - HIGH confidence
- crawl4ai official documentation at docs.crawl4ai.com (Context7: `/websites/crawl4ai`) - HIGH confidence
- FastMCP lifespan pattern from SDK README examples - HIGH confidence
- PyPI JSON API for version checking - HIGH confidence
- Perplexity research on MCP server architecture patterns - MEDIUM confidence
- Perplexity research on safe update mechanisms for Python servers - MEDIUM confidence

---
*Architecture research for: Python MCP server wrapping crawl4ai*
*Researched: 2026-02-19*
