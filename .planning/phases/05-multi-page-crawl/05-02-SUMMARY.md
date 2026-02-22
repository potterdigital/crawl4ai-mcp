---
phase: 05-multi-page-crawl
plan: 02
subsystem: api
tags: [crawl4ai, deep-crawl, bfs, filter-chain, mcp-tool]

# Dependency graph
requires:
  - phase: 03-profile-system
    provides: "build_run_config with profile merging and _PER_CALL_KEYS"
provides:
  - "deep_crawl MCP tool with BFS link-following, configurable depth/pages/scope"
  - "_format_multi_results helper for multi-page result formatting"
  - "deep_crawl_strategy support in _PER_CALL_KEYS"
affects: [05-multi-page-crawl, phase-5-remaining-plans]

# Tech tracking
tech-stack:
  added: [BFSDeepCrawlStrategy, FilterChain, URLPatternFilter]
  patterns: [per-call-strategy-instantiation, filter-chain-composition, scope-mapping]

key-files:
  created:
    - tests/test_deep_crawl.py
  modified:
    - src/crawl4ai_mcp/server.py
    - src/crawl4ai_mcp/profiles.py

key-decisions:
  - "BFSDeepCrawlStrategy created fresh per tool call (mutable state safety)"
  - "deep_crawl_strategy added to _PER_CALL_KEYS (profile merging preserved)"
  - "_format_multi_results created inline (Plan 01 not yet executed)"
  - "scope parameter maps to include_external boolean (same-domain/same-origin -> False, any -> True)"
  - "No monitor parameter on any dispatcher (stdout corruption prevention)"
  - "Headers/cookies skipped for deep_crawl v1 (documented limitation)"

patterns-established:
  - "Per-call strategy instantiation: BFSDeepCrawlStrategy must never be stored in AppContext"
  - "Filter chain composition: include_pattern -> URLPatternFilter, exclude_pattern -> URLPatternFilter(reverse=True)"
  - "Multi-result formatting: _format_multi_results with depth/parent_url metadata"

requirements-completed: [MULTI-02, MULTI-04]

# Metrics
duration: 3min
completed: 2026-02-22
---

# Phase 5 Plan 02: Deep Crawl Summary

**BFS deep_crawl MCP tool with max_depth/max_pages limits, agent-configurable domain scope, and URL pattern filtering via FilterChain**

## Performance

- **Duration:** 3 min
- **Started:** 2026-02-22T02:34:26Z
- **Completed:** 2026-02-22T02:37:10Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments
- deep_crawl MCP tool with BFSDeepCrawlStrategy for BFS link-following from a seed URL
- Agent-configurable scope (same-domain, same-origin, any) and URL pattern filtering (include/exclude)
- Hard limits via max_depth (default 3) and max_pages (default 100) per user decision
- _format_multi_results helper with depth and parent_url metadata in output
- deep_crawl_strategy added to _PER_CALL_KEYS in profiles.py for profile merge support
- 10 new unit tests (58 total pass, 0 regressions)

## Task Commits

Each task was committed atomically:

1. **Task 1: Add deep_crawl tool with BFS strategy and filter chain** - `c99b371` (feat)
2. **Task 2: Add unit tests for deep_crawl** - `d930212` (test)

## Files Created/Modified
- `src/crawl4ai_mcp/server.py` - Added deep_crawl tool, _format_multi_results helper, BFS imports
- `src/crawl4ai_mcp/profiles.py` - Added deep_crawl_strategy to _PER_CALL_KEYS
- `tests/test_deep_crawl.py` - 10 unit tests for tool registration, BFS imports, filter chain, scope mapping

## Decisions Made
- BFSDeepCrawlStrategy created fresh per call to avoid mutable state leakage between tool calls
- Added deep_crawl_strategy to _PER_CALL_KEYS rather than bypassing build_run_config (preserves profile merging for per-page crawl config)
- Created _format_multi_results inline since Plan 01 (crawl_many) hasn't executed yet
- Scope parameter design: simple string mapping to include_external boolean (no DomainFilter import needed)
- Headers/cookies not supported for deep_crawl v1 (documented in docstring)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Created _format_multi_results helper inline**
- **Found during:** Task 1 (deep_crawl implementation)
- **Issue:** Plan references _format_multi_results from Plan 01, but Plan 01 hasn't executed yet (no SUMMARY exists)
- **Fix:** Created the helper inline in server.py with the spec from Plan 01, including depth and parent_url metadata
- **Files modified:** src/crawl4ai_mcp/server.py
- **Verification:** Lint passes, function exists and is called by deep_crawl
- **Committed in:** c99b371 (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (1 blocking)
**Impact on plan:** Essential for deep_crawl to format results. No scope creep. Plan 01 may need to detect the existing helper when it runs.

## Issues Encountered
None - plan executed cleanly after addressing the missing helper dependency.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- deep_crawl tool complete and tested
- _format_multi_results helper available for crawl_many (Plan 01) and crawl_sitemap (Plan 03)
- Profile system extended with deep_crawl_strategy support

## Self-Check: PASSED

All files exist. All commits verified.

---
*Phase: 05-multi-page-crawl*
*Completed: 2026-02-22*
