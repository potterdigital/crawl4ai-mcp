# Roadmap: crawl4ai MCP Server

## Overview

Seven phases build a local Python MCP server that exposes crawl4ai's full capabilities to Claude Code. Phases 1-3 are strictly ordered by hard dependencies: the server foundation must be solid before the core crawl tool, and the profile system must exist before any additional tools are added. Phases 4-6 follow from there — extraction, then multi-page and batch crawls, then authenticated sessions. Phase 7 closes with update management and admin tooling, which is independent of crawl functionality and low-risk.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 1: Foundation** - Server scaffolding, browser lifecycle, stdio hygiene, and Claude Code registration (completed 2026-02-20)
- [x] **Phase 2: Core Crawl** - Single-URL crawl tool with full parameter control and clean markdown output (completed 2026-02-20)
- [x] **Phase 3: Profile System** - Named crawl profiles with per-call override merging (completed 2026-02-20)
- [ ] **Phase 4: Extraction** - LLM-powered and CSS-based structured data extraction tools
- [ ] **Phase 5: Multi-Page Crawl** - Parallel batch crawling, deep BFS crawl, and sitemap crawl
- [ ] **Phase 6: Authentication & Sessions** - Cookie injection and named browser session management
- [ ] **Phase 7: Update Management** - Version checking, startup warnings, and offline update script

## Phase Details

### Phase 1: Foundation
**Goal**: A running MCP server that Claude Code can connect to via stdio, with correct browser lifecycle management and zero stdout corruption
**Depends on**: Nothing (first phase)
**Requirements**: INFRA-01, INFRA-02, INFRA-03, INFRA-04, INFRA-05
**Success Criteria** (what must be TRUE):
  1. Claude Code connects to the server via stdio transport and the tool list is returned with no errors
  2. The server starts and shuts down cleanly — no orphaned browser processes remain after shutdown
  3. Triggering a deliberate crawler error returns a structured error response to Claude rather than crashing the server
  4. All server output (logs, errors, debug messages) appears in stderr only — stdout contains only valid MCP protocol frames
  5. README contains a copy-pasteable Claude Code MCP config snippet that registers the server correctly
**Plans**: 3 plans

Plans:
- [x] 01-01-PLAN.md — uv project setup, FastMCP server scaffold, stderr-only logging, stub ping tool (completed 2026-02-20)
- [x] 01-02-PLAN.md — AsyncWebCrawler singleton via FastMCP lifespan, AppContext, graceful error handling (completed 2026-02-20)
- [x] 01-03-PLAN.md — README with copy-pasteable config snippet, Claude Code registration and verification (completed 2026-02-20)

### Phase 2: Core Crawl
**Goal**: Claude Code can crawl any URL and receive clean, filtered markdown content with full control over JS rendering, request parameters, cache, and content scoping
**Depends on**: Phase 1
**Requirements**: CORE-01, CORE-02, CORE-03, CORE-04, CORE-05
**Success Criteria** (what must be TRUE):
  1. Claude can call the crawl tool with a URL and receive fit_markdown output with navigation and noise elements filtered out
  2. Claude can toggle JS rendering, inject arbitrary JS code, and wait for a CSS selector before content is extracted — all in a single call
  3. Claude can pass custom headers, cookies, user-agent, and timeout values per call and they are applied to that crawl only
  4. Claude can control cache behavior (bypass, force-refresh, use-cached) and the tool respects the instruction
  5. Claude can specify CSS include/exclude selectors and only the matching content is returned
**Plans**: 2 plans

Plans:
- [x] 02-01-PLAN.md — implement crawl_url tool with all params: _build_run_config, _crawl_with_overrides helpers, full CORE-01-05 coverage in server.py (completed 2026-02-20)
- [x] 02-02-PLAN.md — smoke test with real crawl + README update documenting crawl_url and its parameters (completed 2026-02-20)

### Phase 3: Profile System
**Goal**: Named crawl profiles (fast, js-heavy, stealth) ship with the server and any crawl tool call can select a profile as a starting point with per-call overrides applied on top
**Depends on**: Phase 2
**Requirements**: PROF-01, PROF-02, PROF-03, PROF-04
**Success Criteria** (what must be TRUE):
  1. Calling the crawl tool with `profile="fast"` applies the fast profile's configuration (no JS, minimal timeout)
  2. Calling the crawl tool with `profile="stealth"` applies the stealth profile (human-like delays, anti-bot headers)
  3. Any per-call parameter override takes precedence over the selected profile's value (merge order is default -> profile -> per-call)
  4. Claude can call `list_profiles` and see all available profiles with their full configuration
  5. Adding a new YAML file to the `profiles/` directory makes it available as a profile name without any code changes
**Plans**: 3 plans

Plans:
- [x] 03-01-PLAN.md — ProfileManager class + build_run_config merge logic (TDD: default → profile → per-call, verbose=False enforcement, unknown-key stripping) (completed 2026-02-20)
- [x] 03-02-PLAN.md — Four built-in YAML profiles (default, fast, js_heavy, stealth) + server.py wiring (AppContext, crawl_url profile param, _build_run_config replaced) (completed 2026-02-20)
- [x] 03-03-PLAN.md — list_profiles MCP tool + human-verified smoke test (PROF-02, PROF-03) (completed 2026-02-20)

### Phase 4: Extraction
**Goal**: Claude can extract structured JSON from any page using either LLM-powered extraction (opt-in, with cost warning) or deterministic CSS/selector extraction (free, no LLM)
**Depends on**: Phase 3
**Requirements**: EXTR-01, EXTR-02, EXTR-03, EXTR-04
**Success Criteria** (what must be TRUE):
  1. Claude can call `extract_structured` with a JSON schema and instruction and receive typed JSON back — LLM extraction is never triggered by the crawl tool
  2. The `extract_structured` tool description prominently warns about LLM API cost before Claude invokes it
  3. Claude can call `extract_css` with a baseSelector and field definitions and receive structured JSON with no LLM call and no API cost
  4. LLM API keys are sourced from server-side environment variables — no key is ever passed as a tool parameter
**Plans**: 2 plans

Plans:
- [ ] 04-01-PLAN.md — extract_structured tool (LLM-powered, opt-in, cost warning, env-var key sourcing, _check_api_key helper, unit tests)
- [ ] 04-02-PLAN.md — extract_css tool (JsonCssExtractionStrategy, deterministic, no LLM, no cost); EXTR-03 enforcement tests

### Phase 5: Multi-Page Crawl
**Goal**: Claude can crawl dozens of URLs in parallel, follow links BFS-style to a configurable depth, and harvest all URLs from a sitemap — with hard limits preventing runaway crawls
**Depends on**: Phase 3
**Requirements**: MULTI-01, MULTI-02, MULTI-03, MULTI-04
**Success Criteria** (what must be TRUE):
  1. Claude can pass a list of URLs to `crawl_many` and receive all results concurrently — not sequentially
  2. Claude can call `deep_crawl` with a start URL, max_depth, and max_pages and the crawl stops at those hard limits even if more links exist
  3. Claude can call `crawl_sitemap` with a sitemap XML URL and receive crawl results for all discovered URLs
  4. A deep crawl or batch crawl that would exceed the page count or runtime limit is automatically cut off with a summary of what was collected before the limit
**Plans**: TBD

Plans:
- [ ] 05-01: crawl_many tool (arun_many with SemaphoreDispatcher, concurrency cap, result aggregation)
- [ ] 05-02: deep_crawl tool (BFS strategy, hard max_depth/max_pages caps, tool-level timeout)
- [ ] 05-03: crawl_sitemap tool (fetch + parse sitemap XML, crawl discovered URLs via crawl_many)

### Phase 6: Authentication & Sessions
**Goal**: Claude can inject cookies into any single crawl call and can create persistent named browser sessions that survive across multiple tool calls for multi-step authenticated workflows
**Depends on**: Phase 2
**Requirements**: AUTH-01, AUTH-02, AUTH-03
**Success Criteria** (what must be TRUE):
  1. Claude can pass a cookies dict to any crawl tool call and those cookies are applied to that request only — they do not persist to subsequent calls
  2. Claude can create a named session, make multiple crawl calls referencing that session name, and the browser maintains cookies and state across all of them
  3. Claude can call `list_sessions` to see all active named sessions and `destroy_session` to close one
**Plans**: TBD

Plans:
- [ ] 06-01: Cookie injection param on crawl tool (per-call, non-persistent); AUTH-01
- [ ] 06-02: Named session creation, session-scoped crawl calls, list_sessions and destroy_session tools

### Phase 7: Update Management
**Goal**: Claude can check whether a newer crawl4ai version is available mid-session, the server warns on startup if outdated, and a safe offline update script handles upgrades without in-process pip calls
**Depends on**: Phase 1
**Requirements**: UPDT-01, UPDT-02, UPDT-03
**Success Criteria** (what must be TRUE):
  1. Claude can call `check_update` and see the installed version, the latest PyPI version, and a changelog summary — the tool never performs the upgrade itself
  2. Starting the server when a newer crawl4ai version exists logs a visible warning to stderr before the server becomes ready
  3. Running `scripts/update.sh` upgrades crawl4ai, re-installs Playwright if needed, and prints confirmation that the server still starts correctly
**Plans**: TBD

Plans:
- [ ] 07-01: check_update tool (PyPI JSON API, importlib.metadata version compare, changelog summary)
- [ ] 07-02: Startup version check with stderr warning; scripts/update.sh (uv/pip upgrade + playwright install + smoke test)

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6 → 7
Note: Phase 6 depends only on Phase 2 and Phase 7 depends only on Phase 1 — these can be worked after their dependencies complete regardless of the other phases' status.

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Foundation | 3/3 | Complete | 2026-02-20 |
| 2. Core Crawl | 2/2 | Complete | 2026-02-20 |
| 3. Profile System | 3/3 | Complete | 2026-02-20 |
| 4. Extraction | 0/2 | Not started | - |
| 5. Multi-Page Crawl | 0/3 | Not started | - |
| 6. Authentication & Sessions | 0/2 | Not started | - |
| 7. Update Management | 0/2 | Not started | - |
