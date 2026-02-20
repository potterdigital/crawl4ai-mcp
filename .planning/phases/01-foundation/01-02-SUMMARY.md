---
phase: 01-foundation
plan: 02
subsystem: infra
tags: [crawl4ai, asyncwebcrawler, fastmcp, lifespan, browser-lifecycle]

# Dependency graph
requires:
  - phase: 01-01
    provides: FastMCP scaffold with stub lifespan and stderr-only logging

provides:
  - Production-ready AsyncWebCrawler singleton via FastMCP lifespan
  - BrowserConfig(verbose=False) preventing stdout corruption
  - crawler.start()/close() in try/finally guaranteeing clean shutdown
  - AppContext dataclass with typed AsyncWebCrawler field
  - _format_crawl_error helper for structured error returns
  - Upgraded ping tool verifying crawler liveness

affects: [02-crawl-tools, 03-extract-tools, 04-deep-crawl, 05-perf-tuning]

# Tech tracking
tech-stack:
  added: [crawl4ai.AsyncWebCrawler, crawl4ai.BrowserConfig]
  patterns:
    - "Lifespan singleton: one Chromium process shared across all tool calls"
    - "Structured error returns: _format_crawl_error string instead of exceptions"
    - "Explicit start/close over async-with: required when lifespan IS the context manager"

key-files:
  created: []
  modified:
    - src/crawl4ai_mcp/server.py

key-decisions:
  - "explicit crawler.start()/crawler.close() in try/finally (not async with AsyncWebCrawler()) — lifespan IS the context manager"
  - "BrowserConfig(verbose=False) is explicit and mandatory — verbose=True writes to stdout, corrupting MCP transport"
  - "_format_crawl_error returns structured string (not raises) — lets Claude reason about failures"

patterns-established:
  - "Browser singleton: AsyncWebCrawler created once at startup, reused for all tools, closed at shutdown"
  - "Structured error return: _format_crawl_error(url, result) -> multi-line string with URL, HTTP status, error message"

requirements-completed: [INFRA-03, INFRA-04]

# Metrics
duration: 2min
completed: 2026-02-20
---

# Phase 1 Plan 2: AsyncWebCrawler Lifespan Singleton Summary

**AsyncWebCrawler singleton initialized once at startup with BrowserConfig(verbose=False), yielded via AppContext, and closed in finally block — one Chromium process, zero stdout corruption, zero orphaned processes**

## Performance

- **Duration:** 2 min
- **Started:** 2026-02-20T04:43:21Z
- **Completed:** 2026-02-20T04:44:54Z
- **Tasks:** 2 (1 implementation + 1 behavioral verification)
- **Files modified:** 1

## Accomplishments

- Replaced Plan 01-01 stub lifespan with full production AsyncWebCrawler singleton
- Verified full browser lifecycle: startup message, ready message, shutdown message, shutdown complete — all in stderr
- Confirmed zero orphaned chromium processes after server exit (before: 0, after: 0)
- Verified _format_crawl_error produces structured multi-line output with URL, HTTP status, and error message
- Stdout smoke test passed: MCP initialize response is valid JSON, no corruption

## Task Commits

Each task was committed atomically:

1. **Task 1: Replace stub lifespan with AsyncWebCrawler singleton** - `35aebfb` (feat)
2. **Task 2: Behavioral verification** - no commit (verification-only task, no files modified)

**Plan metadata:** (docs commit below)

## Files Created/Modified

- `src/crawl4ai_mcp/server.py` - Full production server: AppContext with typed crawler, lifespan with crawler.start()/close() in try/finally, BrowserConfig(verbose=False), _format_crawl_error helper, upgraded ping tool

## Decisions Made

- Used explicit `crawler.start()` / `crawler.close()` in the lifespan rather than `async with AsyncWebCrawler()` — the lifespan IS the context manager, nesting would break the lifecycle semantics
- `BrowserConfig(verbose=False)` made explicit (not left to default) — verbose=True pollutes stdout, which would silently corrupt the MCP JSON-RPC transport in production
- `_format_crawl_error` returns a structured string rather than raising — this lets Claude Code reason about failures and decide whether to retry, skip, or report

## Lifecycle Verification Results

Full lifecycle log captured in stderr during Task 2 verification:
```
2026-02-19 22:44:17,267 [INFO] __main__: crawl4ai MCP server starting — initializing browser
2026-02-19 22:44:18,222 [INFO] __main__: Browser ready — crawl4ai MCP server is operational
2026-02-19 22:44:18,225 [INFO] __main__: Shutting down browser
2026-02-19 22:44:18,287 [INFO] __main__: Shutdown complete
```

Chromium processes: before=0, after=0. No orphaned processes.

## _format_crawl_error Verification

```
'Crawl failed\nURL: https://example.com/missing\nHTTP status: 404\nError: Not Found'
```

All 4 assertions passed: header present, URL present, status code present, error message present.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

- macOS does not have `timeout` or `gtimeout` commands. Used `perl -e 'alarm N; exec @ARGV'` as a portable timeout substitute for the lifecycle verification runs. This is a macOS environment limitation, not a code issue.

## Next Phase Readiness

- Phase 2 (crawl tools) can directly use `ctx.request_context.lifespan_context.crawler` with no additional setup
- The `_format_crawl_error` pattern is established — Phase 2 tools use it unchanged
- BrowserConfig singleton eliminates per-request Chromium startup latency (2-5 second savings per tool call)

---
*Phase: 01-foundation*
*Completed: 2026-02-20*
