# Requirements: crawl4ai MCP Server

**Defined:** 2026-02-19
**Core Value:** Claude Code can crawl any page, extract any content (markdown or structured JSON), and orchestrate deep multi-page crawls — all through MCP tool calls, without leaving the coding session.

## v1 Requirements

Requirements for initial release. Each maps to roadmap phases.

### Infrastructure

- [ ] **INFRA-01**: Server starts via `uv run` with stdio transport and registers correctly as a Claude Code global MCP server
- [ ] **INFRA-02**: All server output (logs, errors, debug) goes to stderr only — stdout is never written to (protects MCP transport)
- [ ] **INFRA-03**: A single `AsyncWebCrawler` instance is created at server startup via FastMCP lifespan and reused across all tool calls (prevents browser memory leaks)
- [ ] **INFRA-04**: Server handles crawler errors gracefully — returns structured error responses to Claude rather than crashing
- [ ] **INFRA-05**: README documents how to register the server as a Claude Code global MCP server with exact config snippet

### Core Crawl

- [ ] **CORE-01**: User can crawl a single URL and receive clean markdown content (with fit_markdown content filtering applied by default)
- [ ] **CORE-02**: User can control JavaScript rendering per-call (headless/headful toggle, execute arbitrary js_code, wait_for CSS selector before extracting)
- [ ] **CORE-03**: User can pass custom HTTP request parameters per-call (headers dict, cookies dict, user-agent string, timeout seconds)
- [ ] **CORE-04**: User can control crawl4ai cache behavior per-call (bypass cache, force refresh, use cached if available)
- [ ] **CORE-05**: User can specify content extraction scope via CSS selectors (include only matching elements, exclude noise elements)

### Extraction

- [ ] **EXTR-01**: User can extract structured JSON from a page using an LLM (provide Pydantic-compatible JSON schema + natural language instruction; tool warns about LLM cost before executing)
- [ ] **EXTR-02**: User can extract structured JSON from a page using CSS/JSON selectors (deterministic, no LLM cost; baseSelector + field definitions)
- [ ] **EXTR-03**: LLM extraction requires explicit opt-in (no LLM calls happen unless user explicitly calls the extraction tool — never triggered implicitly by the crawl tool)
- [ ] **EXTR-04**: LLM API keys for extraction are read from environment variables on the MCP server process (not passed as tool parameters)

### Multi-Page Crawl

- [x] **MULTI-01**: User can crawl multiple URLs in parallel (pass list of URLs; returns all results concurrently via arun_many())
- [x] **MULTI-02**: User can deep-crawl a site via link-following with BFS strategy (start URL + max_depth + max_pages hard limit; max_pages defaults to 50)
- [x] **MULTI-03**: User can crawl all URLs from a sitemap (pass sitemap XML URL; server fetches, parses, and crawls all discovered URLs)
- [x] **MULTI-04**: Deep crawl and batch crawl enforce hard limits on page count and total runtime to prevent runaway crawls

### Authentication & Sessions

- [x] **AUTH-01**: User can inject cookies into any crawl call (pass cookies dict; applies to the crawl request without persisting to other calls)
- [x] **AUTH-02**: User can create a named session that maintains browser state across multiple crawl calls (login once, reuse session for subsequent authenticated crawls)
- [x] **AUTH-03**: User can list and destroy active sessions via MCP tools

### Profiles

- [ ] **PROF-01**: Server ships with three built-in crawl profiles: `fast` (no JS, minimal timeout), `js-heavy` (full Playwright, extended wait), `stealth` (human-like delays, anti-bot headers)
- [ ] **PROF-02**: User can view all available profiles and their full configuration via a `list_profiles` MCP tool
- [ ] **PROF-03**: User can add/edit custom profiles by creating/modifying YAML files in `profiles/` directory without code changes
- [ ] **PROF-04**: Any crawl tool call can specify a profile name as a starting point with per-call parameter overrides (merge order: default → profile → per-call overrides)

### Update Management

- [x] **UPDT-01**: User can check for crawl4ai updates via a `check_update` MCP tool (queries PyPI JSON API, compares installed vs latest version, shows changelog summary)
- [x] **UPDT-02**: Server logs a warning to stderr on startup if a newer crawl4ai version is available than what is installed
- [x] **UPDT-03**: A `scripts/update.sh` script is provided that safely updates crawl4ai (uv/pip upgrade), re-runs playwright install if needed, and confirms the server still starts correctly

## v2 Requirements

Deferred to future release. Tracked but not in current roadmap.

### Advanced Extraction

- **EXTR-V2-01**: Streaming extraction for very large pages (chunk-by-chunk rather than full-page)
- **EXTR-V2-02**: PDF/document extraction support

### Advanced Crawl

- **MULTI-V2-01**: DFS (depth-first) deep crawl strategy as alternative to BFS
- **MULTI-V2-02**: Crawl with link scoring/filtering (only follow links matching pattern or above relevance score)
- **MULTI-V2-03**: Sitemap crawl with URL filtering (crawl only subset of sitemap URLs)

### Advanced Auth

- **AUTH-V2-01**: Automatic cookie refresh / re-login when session expires

### Observability

- **OBS-V2-01**: Crawl history log (local SQLite) with query tool
- **OBS-V2-02**: Cost tracker for LLM extraction calls

## Out of Scope

| Feature | Reason |
|---------|--------|
| Publishing to PyPI/npm | Local use only — never packaged for distribution |
| Multi-user / server deployment | Runs on localhost for single user only |
| GUI or web dashboard | Claude Code is the interface |
| crawl4ai hooks system (arbitrary Python) | Security risk + unreliable from LLM; expose outcomes as discrete params instead |
| Local embedding strategies (crawl4ai[torch]) | Pulls 2GB+ PyTorch; litellm-based LLM extraction covers the use case |
| Docker/containerization | macOS local, no container required |
| Robots.txt enforcement | User is responsible for compliance; not enforced at MCP layer |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| INFRA-01 | Phase 1 | Pending |
| INFRA-02 | Phase 1 | Pending |
| INFRA-03 | Phase 1 | Pending |
| INFRA-04 | Phase 1 | Pending |
| INFRA-05 | Phase 1 | Pending |
| CORE-01 | Phase 2 | Pending |
| CORE-02 | Phase 2 | Pending |
| CORE-03 | Phase 2 | Pending |
| CORE-04 | Phase 2 | Pending |
| CORE-05 | Phase 2 | Pending |
| PROF-01 | Phase 3 | Pending |
| PROF-02 | Phase 3 | Pending |
| PROF-03 | Phase 3 | Pending |
| PROF-04 | Phase 3 | Pending |
| EXTR-01 | Phase 4 | Pending |
| EXTR-02 | Phase 4 | Pending |
| EXTR-03 | Phase 4 | Pending |
| EXTR-04 | Phase 4 | Pending |
| MULTI-01 | Phase 5 | Complete |
| MULTI-02 | Phase 5 | Complete |
| MULTI-03 | Phase 5 | Complete |
| MULTI-04 | Phase 5 | Complete |
| AUTH-01 | Phase 6 | Complete |
| AUTH-02 | Phase 6 | Complete |
| AUTH-03 | Phase 6 | Complete |
| UPDT-01 | Phase 7 | Complete |
| UPDT-02 | Phase 7 | Complete |
| UPDT-03 | Phase 7 | Complete |

**Coverage:**
- v1 requirements: 28 total
- Mapped to phases: 28
- Unmapped: 0 ✓

---
*Requirements defined: 2026-02-19*
*Last updated: 2026-02-19 — traceability confirmed after roadmap creation*
