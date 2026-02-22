---
phase: 02-core-crawl
plan: 01
subsystem: api
tags: [crawl4ai, mcp, playwright, markdown, content-filtering]

# Dependency graph
requires:
  - phase: 01-foundation
    provides: AsyncWebCrawler singleton, AppContext, _format_crawl_error, FastMCP server scaffold
provides:
  - crawl_url MCP tool with full single-URL crawl capability
  - _build_run_config helper (CrawlerRunConfig + PruningContentFilter pipeline)
  - _crawl_with_overrides helper (per-request header/cookie injection via Playwright hooks)
affects: [03-profiles, 04-extraction, 05-multi-page, 06-auth]

# Tech tracking
tech-stack:
  added:
    - CacheMode (crawl4ai 0.8.0) — cache behavior control enum
    - CrawlerRunConfig (crawl4ai 0.8.0) — per-request crawl configuration
    - DefaultMarkdownGenerator (crawl4ai 0.8.0) — HTML-to-markdown pipeline
    - PruningContentFilter (crawl4ai.content_filter_strategy) — noise reduction for fit_markdown
  patterns:
    - _build_run_config centralizes CrawlerRunConfig construction with verbose=False enforced
    - Hook-based per-request header/cookie injection with try/finally cleanup
    - Cache mode string-to-enum mapping with graceful fallback and warning log
    - fit_markdown with raw_markdown fallback pattern for result extraction

key-files:
  created: []
  modified:
    - src/crawl4ai_mcp/server.py

key-decisions:
  - "CacheMode.ENABLED as tool default (not CrawlerRunConfig's own BYPASS default) — more useful for repeated queries"
  - "page_timeout exposed as seconds in tool interface, multiplied by 1000 internally (CrawlerRunConfig expects ms)"
  - "cookies accepted as list[dict] and passed through to Playwright raw — let Playwright validate shape"
  - "word_count_threshold exposed as crawl_url param (not hardcoded) so Claude can tune PruningContentFilter aggressiveness"

patterns-established:
  - "_build_run_config pattern: always pass verbose=False to CrawlerRunConfig — defaults to True which corrupts MCP stdout"
  - "Hook injection pattern: set hook before arun, clear in finally block — prevents hook leakage across calls"
  - "Result access pattern: result.markdown.fit_markdown (not result.fit_markdown) — deprecated property raises AttributeError in 0.8.0"

requirements-completed: [CORE-01, CORE-02, CORE-03, CORE-04, CORE-05]

# Metrics
duration: 1min
completed: 2026-02-20
---

# Phase 2 Plan 01: Core Crawl Summary

**crawl_url MCP tool with PruningContentFilter fit_markdown output, CacheMode control, CSS scoping, JS execution, and per-request header/cookie injection via Playwright hooks**

## Performance

- **Duration:** 1 min
- **Started:** 2026-02-20T05:16:14Z
- **Completed:** 2026-02-20T05:17:30Z
- **Tasks:** 2
- **Files modified:** 1

## Accomplishments

- crawl_url MCP tool registered with 11 user-facing parameters covering all 5 Phase 2 requirements
- _build_run_config helper centralizes CrawlerRunConfig construction with verbose=False and PruningContentFilter pipeline enforced on every call
- _crawl_with_overrides helper injects per-request headers/cookies via Playwright before_goto/on_page_context_created hooks with guaranteed cleanup in finally block

## Task Commits

Each task was committed atomically:

1. **Task 1: Add imports and helper functions** - `d676199` (feat)
2. **Task 2: Add crawl_url tool** - `9f8a0dc` (feat)

**Plan metadata:** (docs commit follows)

## Files Created/Modified

- `src/crawl4ai_mcp/server.py` - Added CacheMode/CrawlerRunConfig/DefaultMarkdownGenerator/PruningContentFilter imports; added _build_run_config, _crawl_with_overrides helpers; added crawl_url @mcp.tool()

## Decisions Made

- CacheMode.ENABLED as the crawl_url default (not CrawlerRunConfig's own BYPASS default) — more natural for repeated Claude queries where cache hits are desirable
- page_timeout exposed as seconds in the tool interface (human-friendly), multiplied by 1000 internally since CrawlerRunConfig expects milliseconds
- cookies accepted as list[dict] and passed through raw to Playwright's context.add_cookies() — let Playwright validate shape rather than duplicating validation
- word_count_threshold exposed as a crawl_url parameter rather than hardcoded, allowing Claude to tune PruningContentFilter aggressiveness per call

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- crawl_url is fully functional and registered on the MCP server
- _build_run_config and _crawl_with_overrides provide the foundation for Phase 3 profile system (profiles will call _build_run_config with pre-merged params)
- Phase 3 (Profile System) can begin — _build_run_config signature is designed to accept exactly the params a profile merge would produce

---
*Phase: 02-core-crawl*
*Completed: 2026-02-20*
