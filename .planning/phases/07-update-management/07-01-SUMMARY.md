---
phase: 07-update-management
plan: 01
subsystem: admin
tags: [pypi, version-check, httpx, packaging, asyncio]

# Dependency graph
requires:
  - phase: 01-foundation
    provides: FastMCP server scaffold, app_lifespan, tool registration pattern
provides:
  - check_update MCP tool for mid-session version checking
  - _get_latest_pypi_version helper (PyPI JSON API)
  - _fetch_changelog_summary helper (GitHub changelog extraction)
  - _startup_version_check fire-and-forget background task
affects: [07-update-management]

# Tech tracking
tech-stack:
  added: [packaging.version, importlib.metadata]
  patterns: [fire-and-forget asyncio.create_task for non-blocking startup checks, structured error returns for network failures]

key-files:
  created: [tests/test_update.py]
  modified: [src/crawl4ai_mcp/server.py]

key-decisions:
  - "httpx.AsyncClient created and closed within each helper (no persistent client) to avoid lifecycle issues"
  - "Startup version check uses 5s timeout (tighter than tool's 10s) to minimize startup impact"
  - "check_update catches both httpx.HTTPError and httpx.TimeoutException separately for clear error messages"
  - "_startup_version_check wraps entire body in try/except Exception: pass to guarantee server startup"
  - "Changelog parsing extracts ### headers and - ** bullets, truncated to 20 lines"

patterns-established:
  - "Fire-and-forget startup checks: asyncio.create_task() before yield in app_lifespan"
  - "PyPI version comparison: importlib.metadata.version() + packaging.version.Version"

requirements-completed: [UPDT-01, UPDT-02]

# Metrics
duration: 2min
completed: 2026-02-22
---

# Phase 7 Plan 1: Update Management Summary

**check_update MCP tool comparing installed vs PyPI crawl4ai version with changelog highlights, plus fire-and-forget startup warning via asyncio.create_task**

## Performance

- **Duration:** 2 min
- **Started:** 2026-02-22T04:04:26Z
- **Completed:** 2026-02-22T04:06:53Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments
- check_update MCP tool returns version comparison for all outcomes (up-to-date, update available, error)
- Startup version check logs warning to stderr without blocking server readiness
- 9 unit tests covering all code paths (no live HTTP calls)
- Full test suite passes (94 tests, zero regressions)

## Task Commits

Each task was committed atomically:

1. **Task 1: Add check_update tool, helpers, and startup version check** - `f69b5af` (feat)
2. **Task 2: Add unit tests for version check logic** - `0fe7c50` (test)

## Files Created/Modified
- `src/crawl4ai_mcp/server.py` - Added check_update tool, _get_latest_pypi_version, _fetch_changelog_summary, _startup_version_check, and asyncio.create_task wiring in app_lifespan
- `tests/test_update.py` - 9 unit tests covering up-to-date, update available, PyPI unreachable, PyPI timeout, changelog success, changelog fallback, startup warning, no warning, swallows exceptions

## Decisions Made
- httpx.AsyncClient created and closed within each helper function (no persistent client) to avoid lifecycle complexity
- Startup version check uses a tighter 5s timeout vs the tool's 10s to minimize server startup impact
- Changelog parsing extracts category headers and first-level bullets, truncated to 20 lines for readability
- _startup_version_check wraps entire body in bare except to guarantee server startup is never disrupted

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- check_update tool is registered and functional
- Startup warning is wired in via asyncio.create_task
- Plan 07-02 (scripts/update.sh) can proceed independently

## Self-Check: PASSED

All files exist. All commit hashes verified.

---
*Phase: 07-update-management*
*Completed: 2026-02-22*
