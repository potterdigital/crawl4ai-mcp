---
phase: 05-multi-page-crawl
plan: 01
subsystem: api
tags: [crawl4ai, arun_many, SemaphoreDispatcher, batch-crawl, concurrency]

# Dependency graph
requires:
  - phase: 03-profile-system
    provides: build_run_config and ProfileManager for config merging
provides:
  - crawl_many MCP tool for parallel batch URL crawling
  - _format_multi_results shared helper for multi-URL result formatting
  - deep_crawl_strategy in _PER_CALL_KEYS for Plan 02
affects: [05-multi-page-crawl]

# Tech tracking
tech-stack:
  added: [crawl4ai.async_dispatcher.SemaphoreDispatcher]
  patterns: [arun_many + SemaphoreDispatcher for batch crawling, _format_multi_results shared formatter]

key-files:
  created: [tests/test_crawl_many.py]
  modified: [src/crawl4ai_mcp/server.py, src/crawl4ai_mcp/profiles.py]

key-decisions:
  - "SemaphoreDispatcher with no monitor or rate_limiter — predictable concurrency without stdout corruption risk"
  - "Skip headers/cookies for crawl_many v1 — arun_many manages its own sessions; document limitation in docstring"
  - "deep_crawl_strategy added to _PER_CALL_KEYS (not KNOWN_KEYS) — per-call only, never in YAML profiles"

patterns-established:
  - "_format_multi_results: shared helper for multi-URL output, reusable by crawl_sitemap and deep_crawl"
  - "SemaphoreDispatcher(semaphore_count=N) pattern with NO monitor param for MCP transport safety"

requirements-completed: [MULTI-01, MULTI-04]

# Metrics
duration: 2min
completed: 2026-02-22
---

# Phase 5 Plan 1: crawl_many Summary

**Parallel batch crawling via crawl_many tool with SemaphoreDispatcher concurrency control and shared _format_multi_results helper**

## Performance

- **Duration:** 2 min
- **Started:** 2026-02-22T02:34:15Z
- **Completed:** 2026-02-22T02:36:53Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments
- crawl_many MCP tool with agent-configurable concurrency via max_concurrent parameter
- _format_multi_results shared helper that always returns both successes and failures
- deep_crawl_strategy added to _PER_CALL_KEYS for Plan 02 deep_crawl support
- 6 new unit tests (54 total pass, 0 regressions)

## Task Commits

Each task was committed atomically:

1. **Task 1: Add crawl_many tool, _format_multi_results helper, update _PER_CALL_KEYS** - `641d95a` (feat)
2. **Task 2: Add unit tests for crawl_many** - `6c38880` (test)

## Files Created/Modified
- `src/crawl4ai_mcp/server.py` - Added SemaphoreDispatcher import, _format_multi_results helper, crawl_many MCP tool
- `src/crawl4ai_mcp/profiles.py` - Added deep_crawl_strategy to _PER_CALL_KEYS frozenset
- `tests/test_crawl_many.py` - 6 unit tests for tool registration, result formatting, depth metadata, _PER_CALL_KEYS

## Decisions Made
- SemaphoreDispatcher with no monitor or rate_limiter — predictable concurrency for MCP server, no stdout corruption risk from Rich Console
- Headers/cookies skipped for crawl_many v1 — arun_many manages its own Playwright sessions, hooks may not persist; documented in docstring
- deep_crawl_strategy added to _PER_CALL_KEYS (not KNOWN_KEYS) since it is per-call only and should never appear in YAML profiles

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- _format_multi_results is ready for reuse by deep_crawl (Plan 02) and crawl_sitemap (Plan 03)
- deep_crawl_strategy passes through build_run_config without being stripped
- SemaphoreDispatcher pattern established for all batch operations

## Self-Check: PASSED

- All 3 modified/created files exist on disk
- Commit 641d95a (feat) exists in git log
- Commit 6c38880 (test) exists in git log

---
*Phase: 05-multi-page-crawl*
*Completed: 2026-02-22*
