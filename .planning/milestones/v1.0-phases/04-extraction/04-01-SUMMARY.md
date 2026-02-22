---
phase: 04-extraction
plan: 01
subsystem: api
tags: [llm, extraction, litellm, crawl4ai, mcp-tool, json-schema]

# Dependency graph
requires:
  - phase: 02-core-crawl
    provides: "AsyncWebCrawler singleton, _crawl_with_overrides helper, _format_crawl_error pattern"
provides:
  - "extract_structured MCP tool for LLM-powered JSON extraction"
  - "_check_api_key helper for env var pre-validation"
  - "PROVIDER_ENV_VARS mapping for known LLM providers"
affects: [04-extraction, 05-multi-page]

# Tech tracking
tech-stack:
  added: [LLMExtractionStrategy, LLMConfig]
  patterns: [direct-CrawlerRunConfig-construction-for-extraction, env-var-pre-validation, token-usage-via-total_usage]

key-files:
  created:
    - tests/test_extraction.py
  modified:
    - src/crawl4ai_mcp/server.py

key-decisions:
  - "Direct CrawlerRunConfig construction (Option A) for extraction tools — no profile merging or markdown_generator needed"
  - "Token usage via strategy.total_usage attributes — never strategy.show_usage() which calls print()"
  - "PROVIDER_ENV_VARS pre-validation catches missing keys before LLM call attempt"

patterns-established:
  - "Extraction tools construct CrawlerRunConfig directly, not through build_run_config"
  - "LLM tools pre-validate API keys via _check_api_key before any network call"
  - "verbose=False on both strategy AND CrawlerRunConfig — non-negotiable MCP transport safety"

requirements-completed: [EXTR-01, EXTR-03, EXTR-04]

# Metrics
duration: 3min
completed: 2026-02-20
---

# Phase 4 Plan 1: LLM Extraction Summary

**extract_structured MCP tool with LLMExtractionStrategy, env-var API key pre-validation, and cost warning docstring**

## Performance

- **Duration:** 3 min
- **Started:** 2026-02-20T14:12:26Z
- **Completed:** 2026-02-20T14:15:33Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments
- Added `extract_structured` MCP tool with LLM-powered JSON extraction from web pages
- Added `_check_api_key` helper that validates env vars before LLM calls, with structured error messages
- Added `PROVIDER_ENV_VARS` mapping covering openai, anthropic, gemini, deepseek, groq, and ollama
- Prominent cost warning in docstring mentioning `extract_css` as free alternative
- 9 unit tests covering key validation, provider mapping, tool registration, and docstring content

## Task Commits

Each task was committed atomically:

1. **Task 1: Add _check_api_key helper and extract_structured tool** - `aa9f5a8` (feat)
2. **Task 2: Add unit tests for extraction** - `6468bdc` (test)

## Files Created/Modified
- `src/crawl4ai_mcp/server.py` - Added extract_structured tool, _check_api_key helper, PROVIDER_ENV_VARS mapping, LLMConfig/LLMExtractionStrategy imports
- `tests/test_extraction.py` - Unit tests for _check_api_key, PROVIDER_ENV_VARS, tool registration, docstring cost warning

## Decisions Made
- Direct CrawlerRunConfig construction for extraction tools (Option A from research) — extraction tools don't need markdown_generator, PruningContentFilter, or profile merging; direct construction is cleaner
- Token usage accessed via `strategy.total_usage` attributes instead of `strategy.show_usage()` — show_usage() calls print() which would corrupt MCP stdout transport
- Pre-validation of API keys before LLM call attempt — returns clear structured error with the expected env var name and export command

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required. API keys are sourced from standard environment variables (e.g., OPENAI_API_KEY) which users set independently.

## Next Phase Readiness
- extract_structured tool is complete and registered
- Ready for Plan 04-02: extract_css (deterministic CSS extraction)
- _check_api_key and PROVIDER_ENV_VARS patterns are reusable if needed

---
*Phase: 04-extraction*
*Completed: 2026-02-20*
