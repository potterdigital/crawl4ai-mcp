# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-19)

**Core value:** Claude Code can crawl any page, extract any content (markdown or structured JSON), and orchestrate deep multi-page crawls — all through MCP tool calls, without leaving the coding session.
**Current focus:** Phase 3 — Profile System

## Current Position

Phase: 3 of 7 (Profile System) — IN PROGRESS
Plan: 1 of 3 in phase (03-01 complete — ProfileManager and build_run_config implemented via TDD)
Status: Plan 03-01 complete; 33 tests pass; ready for Plan 03-02 (YAML profiles + server integration)
Last activity: 2026-02-20 — Completed Plan 03-01: ProfileManager and build_run_config implemented with full TDD test suite

Progress: [████░░░░░░] 35%

## Performance Metrics

**Velocity:**
- Total plans completed: 4
- Average duration: 2 minutes
- Total execution time: 0.10 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 01-foundation | 3/3 | 6 min | 2 min |
| 02-core-crawl | 2/2 | 3 min | 1.5 min |
| 03-profile-system | 1/3 | 2 min | 2 min |

**Recent Trend:**
- Last 5 plans: 2 min, 1 min, 1 min, 2 min, 2 min
- Trend: fast execution

*Updated after each plan completion*

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- [Init]: Python MCP server via FastMCP with stdio transport (crawl4ai is Python-only)
- [Init]: AsyncWebCrawler singleton via FastMCP lifespan (prevents browser memory leaks)
- [Init]: LLM extraction as separate opt-in tool (prevents cost surprises)
- [Init]: Stderr-only logging enforced from day one (protects MCP transport)
- [01-01]: logging.basicConfig(stream=sys.stderr) placed before ALL library imports — non-negotiable transport hygiene
- [01-01]: mcp.run() called bare (no asyncio.run() wrapper) to avoid double event loop error
- [01-01]: crawl4ai pinned to >=0.8.0,<0.9.0; resolved to 0.8.0
- [01-01]: mcp[cli] pinned to >=1.26.0; resolved to 1.26.0
- [Phase 01-foundation]: explicit crawler.start()/close() in try/finally (not async with) — lifespan IS the context manager
- [Phase 01-foundation]: BrowserConfig(verbose=False) mandatory — verbose=True corrupts MCP stdout transport
- [Phase 01-foundation]: _format_crawl_error returns structured string (not raises) so Claude can reason about failures
- [Phase 01-foundation]: --scope user confirmed as correct user-scoped registration flag in claude mcp add-json
- [02-01]: CacheMode.ENABLED as crawl_url default (not CrawlerRunConfig's BYPASS) — more useful for repeated Claude queries
- [02-01]: page_timeout exposed as seconds, multiplied by 1000 internally (CrawlerRunConfig expects ms)
- [02-01]: cookies list[dict] passed raw to Playwright — let Playwright validate shape, not the tool
- [02-01]: word_count_threshold exposed as crawl_url param (not hardcoded) for per-call PruningContentFilter tuning
- [02-01]: CrawlerRunConfig verbose=False CRITICAL — defaults True, writes to stdout via Rich Console, corrupts MCP transport
- [02-02]: Smoke tests should use CacheMode.BYPASS — ENABLED can return stale cache with hash-only raw_markdown if PruningContentFilter config changed since last cache
- [03-01]: KNOWN_KEYS excludes verbose — forced False unconditionally after merge (MCP transport safety)
- [03-01]: word_count_threshold popped from merged dict and routed to PruningContentFilter, not passed directly to CrawlerRunConfig
- [03-01]: _PER_CALL_KEYS frozenset separates per-call params (css_selector, wait_for, etc.) from YAML-profile KNOWN_KEYS to avoid spurious unknown-key warnings

### Pending Todos

None.

### Blockers/Concerns

- [Research]: Per-Chromium-tab memory on macOS with text_mode=True not precisely quantified — validate during Phase 5 with ps monitoring
- [Research]: MemoryAdaptiveDispatcher may be overly conservative on a dev machine with other apps; consider SemaphoreDispatcher(permits=3) as default
- [Research]: crawl4ai 0.8.0 deep crawl BFS API stability should be validated against live docs before Phase 5 implementation

## Session Continuity

Last session: 2026-02-20
Stopped at: Completed Plan 03-01 — ProfileManager and build_run_config implemented via TDD; 33 tests pass; ready for Plan 03-02
Resume file: None
