---
phase: 03-profile-system
plan: 02
subsystem: crawl
tags: [crawl4ai, yaml, profiles, CrawlerRunConfig, PruningContentFilter]

# Dependency graph
requires:
  - phase: 03-01
    provides: ProfileManager and build_run_config implemented with TDD test suite

provides:
  - Four built-in YAML profile files (default, fast, js_heavy, stealth)
  - ProfileManager wired into AppContext and initialized in app_lifespan
  - crawl_url with profile: str | None parameter for end-to-end profile selection
  - _build_run_config removed; all config construction via build_run_config from profiles.py

affects:
  - 03-03 (list_profiles tool will surface these profile names/values)
  - 04-extraction (extract tools will also accept profile param)
  - 05-multi-page (batch tools will also accept profile param)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Per-call kwargs built conditionally (None-guard) so profile defaults are not overridden by unset tool params"
    - "page_timeout conversion (seconds * 1000) happens in crawl_url before passing to build_run_config which expects ms"
    - "ProfileManager instantiated once in app_lifespan alongside crawler; both held in AppContext dataclass"

key-files:
  created:
    - src/crawl4ai_mcp/profiles/default.yaml
    - src/crawl4ai_mcp/profiles/fast.yaml
    - src/crawl4ai_mcp/profiles/js_heavy.yaml
    - src/crawl4ai_mcp/profiles/stealth.yaml
  modified:
    - src/crawl4ai_mcp/server.py

key-decisions:
  - "CrawlerRunConfig still imported in server.py for _crawl_with_overrides type annotation even though config construction moved to profiles.py"
  - "per_call_kwargs uses conditional inclusion (if param is not None / != default) to prevent tool signature defaults from silently overriding profile values"
  - "page_timeout conversion (seconds to ms) done in crawl_url before merge — profiles and per-call params share the same ms unit inside build_run_config"

patterns-established:
  - "Profile-aware tools: build per_call_kwargs dict with sentinel guards, call build_run_config(app.profile_manager, profile, **per_call_kwargs)"

requirements-completed: [PROF-01, PROF-04]

# Metrics
duration: 3min
completed: 2026-02-20
---

# Phase 03 Plan 02: Profile System — YAML Files and Server Integration Summary

**Four built-in YAML profiles (default/fast/js_heavy/stealth) shipped and wired into crawl_url via ProfileManager in AppContext, enabling `crawl_url(url="...", profile="stealth")` end-to-end**

## Performance

- **Duration:** ~3 min
- **Started:** 2026-02-20T05:52:00Z
- **Completed:** 2026-02-20T05:55:07Z
- **Tasks:** 2
- **Files modified:** 5 (4 created, 1 modified)

## Accomplishments

- Created four YAML profile files with descriptive inline comments; all timeout values documented as milliseconds
- Wired ProfileManager into AppContext dataclass and app_lifespan (logs loaded profile count + names at startup)
- Added `profile: str | None = None` param to crawl_url with full docstring describing available profiles
- Replaced inline `_build_run_config` helper with `build_run_config` from profiles.py — no duplicate config logic
- Built per_call_kwargs with None-guard conditionals to prevent tool default values from silently overriding profile settings
- All 33 profile tests continue to pass; ruff clean

## Task Commits

Each task was committed atomically:

1. **Task 1: Create four built-in YAML profile files** - `2f6aa5c` (feat)
2. **Task 2: Wire ProfileManager into server.py and add profile param to crawl_url** - `2ca8191` (feat)

**Plan metadata:** (docs commit to follow)

## Files Created/Modified

- `src/crawl4ai_mcp/profiles/default.yaml` - Base profile: domcontentloaded, 60s timeout, word_count_threshold=10
- `src/crawl4ai_mcp/profiles/fast.yaml` - Fast profile: domcontentloaded, 15s timeout, word_count_threshold=5
- `src/crawl4ai_mcp/profiles/js_heavy.yaml` - JS-heavy profile: networkidle, 90s timeout, scan_full_page, lazy-load support
- `src/crawl4ai_mcp/profiles/stealth.yaml` - Stealth profile: magic=true, simulate_user, override_navigator, human delays
- `src/crawl4ai_mcp/server.py` - ProfileManager in AppContext, profile param in crawl_url, _build_run_config removed

## Decisions Made

- **CrawlerRunConfig import retained** in server.py — needed for `_crawl_with_overrides` type annotation even though config construction moved to profiles.py
- **Conditional per_call_kwargs** — only include optional params when non-None (or non-default for word_count_threshold) so profile values are not silently overridden by tool signature defaults
- **page_timeout unit** — seconds→ms conversion in crawl_url before building per_call_kwargs; both profile YAML values and per-call overrides share the ms unit inside build_run_config

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Added CrawlerRunConfig back to crawl4ai imports**
- **Found during:** Task 2 (server.py editing)
- **Issue:** When removing `_build_run_config`, also removed `CrawlerRunConfig` and `DefaultMarkdownGenerator` from the crawl4ai import. `_crawl_with_overrides` still uses `CrawlerRunConfig` as a type annotation — import was needed.
- **Fix:** Added `CrawlerRunConfig` back to `from crawl4ai import ...`; `DefaultMarkdownGenerator` and `PruningContentFilter` no longer needed in server.py (both handled inside profiles.py)
- **Files modified:** src/crawl4ai_mcp/server.py
- **Verification:** `uv run python -c "from crawl4ai_mcp.server import crawl_url, AppContext; print('ok')"` printed "ok"; ruff passed
- **Committed in:** `2ca8191` (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (Rule 1 - Bug)
**Impact on plan:** Necessary fix to restore the missing type annotation import that was accidentally removed. No scope creep.

## Issues Encountered

None beyond the auto-fixed import issue above.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Phase 03-02 complete: `crawl_url(url="...", profile="stealth")` now works end-to-end
- Ready for Phase 03-03: `list_profiles` tool to surface loaded profiles and their settings to Claude
- All 33 profile tests pass; ruff clean

## Self-Check: PASSED

- FOUND: src/crawl4ai_mcp/profiles/default.yaml
- FOUND: src/crawl4ai_mcp/profiles/fast.yaml
- FOUND: src/crawl4ai_mcp/profiles/js_heavy.yaml
- FOUND: src/crawl4ai_mcp/profiles/stealth.yaml
- FOUND: src/crawl4ai_mcp/server.py
- FOUND commit: 2f6aa5c (Task 1)
- FOUND commit: 2ca8191 (Task 2)

---
*Phase: 03-profile-system*
*Completed: 2026-02-20*
