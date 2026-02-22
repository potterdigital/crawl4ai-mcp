# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-19)

**Core value:** Claude Code can crawl any page, extract any content (markdown or structured JSON), and orchestrate deep multi-page crawls — all through MCP tool calls, without leaving the coding session.
**Current focus:** Phase 7 complete — Update Management (UPDT-03 satisfied); all 7 phases complete

## Current Position

Phase: 7 of 7 (Update Management) — COMPLETE
Plan: 2 of 2 in phase (07-02 complete — offline update script with Playwright reinstall and smoke test)
Status: Phase 7 complete; all phases complete; project feature-complete
Last activity: 2026-02-22 — Completed Plan 07-02: update script

Progress: [██████████] 100%

## Performance Metrics

**Velocity:**
- Total plans completed: 15
- Average duration: 2 minutes
- Total execution time: 0.56 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 01-foundation | 3/3 | 6 min | 2 min |
| 02-core-crawl | 2/2 | 3 min | 1.5 min |
| 03-profile-system | 3/3 | 9 min | 3 min |
| 04-extraction | 2/2 | 5 min | 2.5 min |
| 05-multi-page-crawl | 3/3 | 7 min | 2.3 min |
| 06-authentication-sessions | 2/2 | 4 min | 2 min |
| 07-update-management | 1/2 | 1 min | 1 min |

**Recent Trend:**
- Last 5 plans: 3 min, 2 min, 2 min, 2 min, 1 min
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
- [03-02]: per_call_kwargs built with None-guards so profile defaults are not overridden by unset tool params
- [03-02]: page_timeout seconds→ms conversion done in crawl_url before merge; profiles and per-call overrides share the ms unit inside build_run_config
- [03-02]: CrawlerRunConfig still imported in server.py for _crawl_with_overrides type annotation even after config construction moved to profiles.py
- [03-03]: list_profiles output uses markdown headers (##) per profile for Claude-readable formatting
- [03-03]: Default profile labeled explicitly as "base layer applied to every crawl" to distinguish from named profiles
- [04-01]: Direct CrawlerRunConfig construction for extraction tools (Option A) — no profile merging or markdown_generator needed
- [04-01]: Token usage via strategy.total_usage attributes — never strategy.show_usage() which calls print()
- [04-01]: PROVIDER_ENV_VARS pre-validation catches missing API keys before LLM call attempt
- [04-02]: Same direct CrawlerRunConfig construction for extract_css — extraction tools bypass profile merging
- [04-02]: verbose=False on both JsonCssExtractionStrategy and CrawlerRunConfig — non-negotiable MCP transport safety
- [04-02]: Empty result check includes "[]" string — JsonCssExtractionStrategy returns "[]" when no selectors match
- [05-01]: SemaphoreDispatcher with no monitor or rate_limiter — predictable concurrency without stdout corruption risk
- [05-01]: Headers/cookies skipped for crawl_many v1 — arun_many manages its own sessions; document limitation
- [05-01]: deep_crawl_strategy added to _PER_CALL_KEYS (not KNOWN_KEYS) — per-call only, never in YAML profiles
- [05-02]: BFSDeepCrawlStrategy created fresh per call — mutable state (_pages_crawled, _cancel_event) prevents reuse
- [05-02]: scope parameter maps to include_external boolean — no DomainFilter import needed
- [05-02]: No monitor parameter on any dispatcher — CrawlerMonitor uses Rich Console which writes to stdout
- [05-02]: Headers/cookies not supported for deep_crawl v1 — documented limitation in docstring
- [05-03]: httpx for sitemap fetching (not browser) — sitemaps are plain XML, no JS rendering needed
- [05-03]: Recursive resolution for sitemap index files — transparent to the caller
- [05-03]: max_urls default 500 — prevents runaway crawls on large sitemaps (50K+ URLs common)
- [06-01]: Sessions tracked as dict[str, float] mapping session_id to creation timestamp for future TTL enforcement
- [06-01]: create_session uses about:blank for cookie-only sessions (no URL) to avoid unnecessary page loads
- [06-01]: Session cleanup uses kill_session in finally block — exceptions caught and ignored to ensure all sessions are attempted
- [06-02]: destroy_session wraps kill_session in try/except — crawl4ai may auto-expire sessions before explicit destruction
- [06-02]: list_sessions sorts sessions alphabetically by session_id for consistent output
- [07-02]: httpx for PyPI version check (already a crawl4ai transitive dep, no new dependency)
- [07-02]: Exit 0 on pin range block (informational, not an error)
- [07-02]: Playwright reinstall only when version actually changed (skip on pin range block)

### Pending Todos

None.

### Blockers/Concerns

- [Research]: Per-Chromium-tab memory on macOS with text_mode=True not precisely quantified — validate during Phase 5 with ps monitoring
- [Research]: MemoryAdaptiveDispatcher may be overly conservative on a dev machine with other apps; consider SemaphoreDispatcher(permits=3) as default
- [Research]: crawl4ai 0.8.0 deep crawl BFS API stability should be validated against live docs before Phase 5 implementation

## Session Continuity

Last session: 2026-02-22
Stopped at: Completed 07-02-PLAN.md — Phase 7 complete; all phases complete; project feature-complete
Resume file: None
