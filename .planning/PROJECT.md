# crawl4ai MCP Server

## What This Is

A local MCP server that wraps crawl4ai and exposes 12 web crawling tools to Claude Code — single-URL crawl, structured extraction (LLM and CSS), multi-page batch/deep/sitemap crawling, authenticated sessions, named profiles, and self-update management. Runs locally via stdio transport, not packaged for distribution.

## Core Value

Claude Code can crawl any page, extract any content (markdown or structured JSON), and orchestrate deep multi-page crawls — all through MCP tool calls, without leaving the coding session.

## Requirements

### Validated

- ✓ Single-URL crawling with clean markdown output — v1.0
- ✓ LLM-powered structured data extraction with user-defined schemas — v1.0
- ✓ CSS-based deterministic structured extraction (no LLM cost) — v1.0
- ✓ Multi-page crawls via sitemap and link-following (configurable depth) — v1.0
- ✓ Parallel/async batch crawling of multiple URLs — v1.0
- ✓ JS rendering support (playwright-based, for dynamic/SPA pages) — v1.0
- ✓ Authenticated crawl support (cookie/session injection) — v1.0
- ✓ Preset crawl profiles (fast, js_heavy, stealth) with per-call overrides — v1.0
- ✓ Update checker: MCP tool that checks crawl4ai version and reports what's new — v1.0
- ✓ Update command: one-shot script that updates crawl4ai and patches anything needed — v1.0

### Active

(None — next milestone requirements TBD)

### Out of Scope

- Publishing to npm/PyPI — local use only, never packaged for distribution
- Multi-user/server deployment — runs on localhost for single user
- GUI or web dashboard — Claude Code is the interface
- crawl4ai hooks system (arbitrary Python) — security risk + unreliable from LLM
- Local embedding strategies (crawl4ai[torch]) — pulls 2GB+ PyTorch; litellm covers the use case
- Docker/containerization — macOS local, no container required
- Robots.txt enforcement — user is responsible for compliance

## Context

Shipped v1.0 with 3,019 LOC (Python + shell).
Tech stack: Python, FastMCP, crawl4ai 0.8.x, Playwright/Chromium, httpx, PyYAML.
12 MCP tools registered, 94 tests passing, 28 requirements satisfied.

**Architecture:** FastMCP server with AsyncWebCrawler singleton (lifespan-managed), YAML profile system with 3-layer merge (default → profile → per-call), and structured error returns.

**Known tech debt (advisory):**
- packaging, httpx, pyyaml used directly but undeclared as direct dependencies (transitive via crawl4ai/mcp)
- _CACHE_MAP dict literal duplicated across 4 crawl tools
- Extraction tools bypass profile system by design

## Constraints

- **Language**: Python (crawl4ai is Python-native; MCP server wraps it in Python)
- **Runtime**: Local machine only — macOS, no containerization required
- **Packaging**: Not for publishing — no pyproject.toml publishing config needed
- **Integration**: Must work as a global Claude Code MCP server (stdio transport)
- **stdout safety**: All logging to stderr; verbose=False enforced on all crawl4ai objects
- **crawl4ai install**: uv for dependency management; playwright installed separately

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Python MCP server (not Node) | crawl4ai is Python-only; wrapping in Python avoids subprocess overhead | ✓ Good — clean integration, no IPC overhead |
| stdio transport | Required for Claude Code global MCP server integration | ✓ Good — works reliably with stderr-only logging |
| Per-tool params + preset profiles | Profiles for common patterns, overrides for fine-tuning | ✓ Good — 3-layer merge (default→profile→per-call) is flexible |
| Update checker as MCP tool | Lets Claude check crawl4ai version mid-session without leaving Claude Code | ✓ Good — non-blocking startup check + on-demand tool |
| AsyncWebCrawler singleton via lifespan | Prevents 2-5s Chromium startup per tool call | ✓ Good — shared across all 12 tools |
| Extraction tools bypass profiles | LLM/CSS extraction have different config needs than crawling | ✓ Good — simpler, no unnecessary profile merging |
| CacheMode.ENABLED as default | More useful for repeated Claude queries than BYPASS | ✓ Good — reduces redundant crawls |
| Structured error returns (not exceptions) | Lets Claude reason about failures and decide how to proceed | ✓ Good — established pattern across all tools |
| verbose=False enforced unconditionally | MCP stdout transport corruption prevention | ✓ Critical — non-negotiable safety constraint |

---
*Last updated: 2026-02-21 after v1.0 milestone*
