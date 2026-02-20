# Stack Research

**Domain:** Local Python MCP Server wrapping crawl4ai
**Researched:** 2026-02-19
**Confidence:** HIGH

## Recommended Stack

### Core Technologies

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| Python | >=3.10 (target 3.12+) | Runtime | Both `mcp` and `crawl4ai` require >=3.10. Target 3.12+ for better asyncio performance and error messages. |
| `mcp` (MCP Python SDK) | 1.26.0 | MCP server framework | Official Anthropic SDK. Provides `FastMCP` class with `@mcp.tool()` decorator, stdio transport out of the box, Pydantic-based schemas. `mcp.run()` defaults to stdio. |
| `crawl4ai` | 0.8.0 | Web crawling engine | Open-source async crawler with LLM extraction, JS rendering via Playwright, structured data extraction, multi-page deep crawl. The core capability we are wrapping. |
| `uv` | 0.10.x | Dependency management + runner | Fast, replaces pip/venv/pip-tools. `uv run` executes within project venv. `uv add` manages `pyproject.toml`. Claude Code can invoke server via `uv run`. |
| Playwright (Chromium) | >=1.49.0 (bundled via crawl4ai) | Browser automation | crawl4ai's JS rendering backend. Installed via `crawl4ai-setup` or `playwright install chromium`. |

### Supporting Libraries

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `pydantic` | >=2.10 | Schema definitions for tool inputs/outputs | Already a dependency of both `mcp` (>=2.11) and `crawl4ai` (>=2.10). Use for defining structured extraction schemas and MCP tool parameter models. |
| `httpx` | >=0.27.1 | HTTP client | Already a dependency of `mcp`. Available if needed for non-browser HTTP requests, API calls, or health checks. Do not use for crawling (crawl4ai handles that). |
| `litellm` | >=1.53.1 | LLM provider abstraction | Bundled with crawl4ai. Powers `LLMExtractionStrategy`. Supports OpenAI, Anthropic, Ollama, and 100+ providers via unified interface. |
| `beautifulsoup4` | ~4.12 | HTML parsing | Bundled with crawl4ai. Used internally for content extraction. May be useful for custom post-processing in tool handlers. |
| `aiohttp` | >=3.11.11 | Async HTTP | Bundled with crawl4ai. Used internally. No need to add separately. |

### Development Tools

| Tool | Purpose | Notes |
|------|---------|-------|
| `uv` | Project management, venv, dependency resolution, script runner | Install globally via `curl -LsSf https://astral.sh/uv/install.sh \| sh` or `brew install uv`. |
| `crawl4ai-setup` | Browser installation | Run after `uv sync` to install Playwright Chromium. Equivalent to `playwright install chromium` plus crawl4ai-specific setup. |
| `crawl4ai-doctor` | Installation verification | Validates that crawl4ai, Playwright, and browsers are correctly configured. Run after setup. |
| MCP Inspector | MCP server testing | `npx @modelcontextprotocol/inspector` launches a web UI to test MCP tools interactively. |
| `claude mcp add-json` | Register server with Claude Code | Registers the server globally so Claude Code can invoke it via stdio. |

## Server Architecture Pattern

### FastMCP (Recommended)

Use `FastMCP` from the MCP Python SDK. It provides decorators, automatic Pydantic schema generation from type hints, and stdio transport by default.

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("crawl4ai-server")

@mcp.tool()
async def crawl_url(url: str, include_links: bool = False) -> str:
    """Crawl a URL and return markdown content."""
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig

    browser_config = BrowserConfig(headless=True)
    run_config = CrawlerRunConfig()

    async with AsyncWebCrawler(config=browser_config) as crawler:
        result = await crawler.arun(url=url, config=run_config)
        return result.markdown

if __name__ == "__main__":
    mcp.run()  # Defaults to stdio transport
```

Key FastMCP APIs:
- `FastMCP(name)` -- server constructor
- `@mcp.tool()` -- register a tool; schema auto-generated from function signature + type hints
- `@mcp.resource(uri_template)` -- register a resource
- `@mcp.prompt()` -- register a prompt template
- `mcp.run()` -- start the server (defaults to stdio)
- `mcp.run(transport="stdio")` -- explicit stdio transport
- `Context` object -- inject via type hint for logging (`ctx.info()`, `ctx.debug()`) and progress reporting (`ctx.report_progress()`)

### Why NOT Low-Level Server

The MCP SDK also exposes `mcp.server.lowlevel.Server` with `@server.list_tools()` / `@server.call_tool()` decorators and manual `stdio_server()` setup. This is unnecessary complexity for our use case. FastMCP provides the same capabilities with far less boilerplate. Use low-level only if you need custom protocol handling (we do not).

## Installation

```bash
# 1. Initialize project with uv
uv init crawl4ai-mcp
cd crawl4ai-mcp

# 2. Set Python version
echo "3.12" > .python-version

# 3. Add core dependencies
uv add "mcp[cli]>=1.26.0"
uv add "crawl4ai>=0.8.0"

# 4. Add dev dependencies
uv add --dev pytest pytest-asyncio

# 5. Install Playwright browsers (required by crawl4ai)
uv run crawl4ai-setup

# 6. Verify installation
uv run crawl4ai-doctor

# 7. Register with Claude Code (global scope)
claude mcp add-json --scope user crawl4ai '{"type":"stdio","command":"uv","args":["run","--directory","/absolute/path/to/crawl4ai-mcp","python","-m","crawl4ai_mcp.server"]}'
```

### Claude Code Registration Notes

- Use `uv run --directory /path/to/project` so Claude Code invokes the correct venv from any working directory
- Use `--scope user` for global availability across all projects (stored in `~/.claude.json`)
- The `"type": "stdio"` transport means Claude Code communicates via stdin/stdout
- Environment variables can be passed via `"env": {"KEY": "value"}` in the JSON config

## Alternatives Considered

| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|-------------------------|
| `uv` for deps | `pip` + `venv` | Never for this project. `uv` is faster, manages venv automatically, produces lockfiles, and `uv run` simplifies Claude Code integration. |
| `FastMCP` (high-level) | `mcp.server.lowlevel.Server` | Only if you need custom protocol-level control (e.g., custom capability negotiation). We do not. |
| `crawl4ai` | `crawlee-python` (Apify) | If you need Apify cloud integration or prefer Crawlee's queue-based architecture. crawl4ai is purpose-built for LLM extraction workflows, which is our primary use case. |
| `crawl4ai` | `playwright` directly | If you only need browser automation without crawl4ai's markdown conversion, extraction strategies, or multi-page crawl orchestration. We need all of those. |
| `litellm` (via crawl4ai) | Direct OpenAI/Anthropic SDKs | If you only target one LLM provider. litellm is already bundled and provides provider-agnostic extraction. |
| `pydantic` v2 | `dataclasses` | Never. Both `mcp` and `crawl4ai` depend on Pydantic v2. Tool schemas are auto-generated from Pydantic models. Use Pydantic for everything. |

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| `pip install` directly | No lockfile, no automatic venv management, slower resolution | `uv add` + `uv sync` |
| `crawl4ai[sync]` / Selenium | Deprecated sync mode. Selenium is slower and less capable than Playwright. | Default `crawl4ai` (async + Playwright) |
| `crawl4ai[all]` | Installs PyTorch, transformers, sentence-transformers -- massive deps (~2GB+) that are unnecessary unless you need local embedding-based clustering. | Base `crawl4ai` (includes litellm for LLM extraction) |
| `crawl4ai[torch]` / `crawl4ai[cosine]` | Only needed for `CosineStrategy` clustering with local models. Huge dependency footprint. | Base `crawl4ai` -- use `LLMExtractionStrategy` or `JsonCssExtractionStrategy` instead |
| SSE or HTTP transport | Adds network complexity. Claude Code communicates via stdio for local servers. | `mcp.run()` (stdio is default) |
| `requests` / `urllib3` | Synchronous HTTP. The entire stack is async. | `httpx` (async, already a dependency) |
| Manual `asyncio.run()` in server | FastMCP handles the event loop. Do not wrap `mcp.run()` in `asyncio.run()`. | `mcp.run()` directly in `__main__` |
| Global Playwright install (`playwright install`) | May install all browsers (Chromium + Firefox + WebKit). | `crawl4ai-setup` (installs only what crawl4ai needs) or `playwright install chromium` |

## Stack Patterns

**For single-URL crawl tools:**
- Use `AsyncWebCrawler` with `BrowserConfig(headless=True)` and basic `CrawlerRunConfig()`
- Return `result.markdown` for clean LLM-friendly content

**For LLM-powered extraction tools:**
- Use `LLMExtractionStrategy` with `LLMConfig(provider="...", api_token="...")`
- Define Pydantic models for the extraction schema
- Pass `schema=Model.model_json_schema()` to the strategy
- Require the caller to provide API key or use environment variables

**For multi-page / deep crawl tools:**
- Use crawl4ai's deep crawl capabilities (v0.8.0+)
- Consider `CrawlerRunConfig` with `exclude_external_links=True` and `word_count_threshold` for focused crawling

**For JS-heavy pages:**
- `BrowserConfig(headless=True)` is the default and handles JS rendering via Playwright
- Use `CrawlerRunConfig(wait_for=...)` for pages that need time to render
- Use `CrawlerRunConfig(js_code=...)` for executing custom JavaScript before extraction

**For authenticated crawls:**
- Use `BrowserConfig` with cookie/header configuration
- Or use `CrawlerRunConfig` with custom headers

## Version Compatibility

| Package | Compatible With | Notes |
|---------|-----------------|-------|
| `mcp` 1.26.0 | Python >=3.10, Pydantic >=2.11 | Pydantic v2 only (not v1). |
| `crawl4ai` 0.8.0 | Python >=3.10, Pydantic >=2.10, Playwright >=1.49 | Both `mcp` and `crawl4ai` require Pydantic v2, so no conflict. |
| `uv` 0.10.x | Python 3.8+ (manages any Python version) | uv itself can run on older Python, but it will create venvs targeting 3.12+. |
| Playwright (Chromium) | crawl4ai 0.8.0 | Installed via `crawl4ai-setup`. Version managed by crawl4ai's dependency pin. |

### Pydantic Version Alignment

Both `mcp` (>=2.11.0,<3.0.0) and `crawl4ai` (>=2.10) require Pydantic v2. uv will resolve to a single compatible version (likely 2.11.x). No conflicts expected.

## Project Structure

```
crawl4ai-mcp/
├── pyproject.toml           # uv project config, dependencies
├── uv.lock                  # Lockfile (auto-generated, commit to repo)
├── .python-version          # "3.12"
├── src/
│   └── crawl4ai_mcp/
│       ├── __init__.py
│       ├── server.py        # FastMCP server, tool registrations
│       ├── tools/           # Individual tool modules
│       │   ├── __init__.py
│       │   ├── crawl.py     # Single URL crawl
│       │   ├── extract.py   # LLM extraction
│       │   ├── deep.py      # Multi-page / deep crawl
│       │   └── ...
│       └── config.py        # Shared configuration, browser defaults
└── tests/
    └── ...
```

## Sources

- Context7 `/modelcontextprotocol/python-sdk` (v1.12.4 docs, Benchmark 86.8) -- FastMCP API, tool decorators, stdio transport, server initialization
- Context7 `/websites/crawl4ai` (Benchmark 90.7) -- AsyncWebCrawler usage, BrowserConfig, CrawlerRunConfig, LLMExtractionStrategy, installation
- PyPI `mcp` package page (https://pypi.org/project/mcp/) -- version 1.26.0, dependencies confirmed
- PyPI `crawl4ai` package page (https://pypi.org/project/crawl4ai/) -- version 0.8.0, extras, dependencies confirmed
- PyPI `uv` package page (https://pypi.org/project/uv/) -- version 0.10.4
- Perplexity search -- crawl4ai installation methods, MCP SDK version, uv best practices, Claude Code MCP registration
- Official uv docs (https://docs.astral.sh/uv/guides/projects/) -- project initialization, `uv add`, `uv sync`, `uv run`
- MCP official docs (https://modelcontextprotocol.io/docs/develop/connect-local-servers) -- stdio transport, server registration

---
*Stack research for: crawl4ai MCP Server*
*Researched: 2026-02-19*
