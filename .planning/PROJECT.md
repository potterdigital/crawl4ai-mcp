# crawl4ai MCP Server

## What This Is

A local MCP (Model Context Protocol) server that exposes the full capabilities of the crawl4ai framework as tools for use with Claude Code. The server runs on the local machine as a global MCP server — not packaged for distribution. It gives Claude Code powerful web crawling, content extraction, structured data extraction, and multi-page crawling abilities, with built-in support for checking and applying crawl4ai updates.

## Core Value

Claude Code can crawl any page, extract any content (markdown or structured JSON), and orchestrate deep multi-page crawls — all through MCP tool calls, without leaving the coding session.

## Requirements

### Validated

(None yet — ship to validate)

### Active

- [ ] Single-URL crawling with clean markdown output
- [ ] LLM-powered structured data extraction with user-defined schemas
- [ ] Multi-page crawls via sitemap and link-following (configurable depth)
- [ ] Parallel/async batch crawling of multiple URLs
- [ ] JS rendering support (playwright-based, for dynamic/SPA pages)
- [ ] Authenticated crawl support (cookie/session injection)
- [ ] Preset crawl profiles (fast, js-heavy, stealth) with per-call overrides
- [ ] Update checker: MCP tool that checks crawl4ai version and reports what's new
- [ ] Update command: one-shot script that updates crawl4ai and patches anything needed

### Out of Scope

- Publishing to npm/PyPI — local use only, never packaged for distribution
- Multi-user/server deployment — runs on localhost for single user
- GUI or web dashboard — Claude Code is the interface

## Context

- **Framework**: crawl4ai (Python) — async, playwright-based web crawler with LLM extraction support. Docs: https://docs.crawl4ai.com/. Repo: https://github.com/unclecode/crawl4ai
- **MCP protocol**: The MCP server will wrap crawl4ai's Python API and expose it as tools. Claude Code connects to it as a global MCP server.
- **Crawl targets**: Technical docs/wikis, JS-rendered SPAs, general public web, structured/API-like pages, and authenticated sites (cookie injection)
- **Output formats needed**: Clean markdown (for general reading) and structured JSON (for typed data extraction)
- **Update lifecycle**: crawl4ai is actively developed — the server must handle framework updates gracefully and expose a way to check + apply updates

## Constraints

- **Language**: Python (crawl4ai is Python-native; MCP server wraps it in Python)
- **Runtime**: Local machine only — macOS, no containerization required
- **Packaging**: Not for publishing — no pyproject.toml publishing config needed
- **Integration**: Must work as a global Claude Code MCP server (stdio transport)
- **crawl4ai install**: Use uv or pip for dependency management; playwright installed separately

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Python MCP server (not Node) | crawl4ai is Python-only; wrapping in Python avoids subprocess overhead | — Pending |
| stdio transport | Required for Claude Code global MCP server integration | — Pending |
| Per-tool params + preset profiles | Profiles for common patterns, overrides for fine-tuning | — Pending |
| Update checker as MCP tool | Lets Claude check crawl4ai version mid-session without leaving Claude Code | — Pending |

---
*Last updated: 2026-02-19 after initialization*
