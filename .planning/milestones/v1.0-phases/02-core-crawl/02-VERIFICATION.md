---
phase: 02-core-crawl
verified: 2026-02-20T05:28:50Z
status: passed
score: 5/5 must-haves verified
---

# Phase 2: Core Crawl Verification Report

**Phase Goal:** Claude Code can crawl any URL and receive clean, filtered markdown content with full control over JS rendering, request parameters, cache, and content scoping.
**Verified:** 2026-02-20T05:28:50Z
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Claude can call the crawl tool with a URL and receive fit_markdown output with navigation and noise elements filtered out | VERIFIED | `crawl_url` returns `md.fit_markdown or md.raw_markdown` (line 278); PruningContentFilter(threshold=0.48) is applied via `_build_run_config` on every call; smoke test confirmed 102 chars fit_markdown from example.com |
| 2 | Claude can toggle JS rendering, inject arbitrary JS code, and wait for a CSS selector before content is extracted — all in a single call | VERIFIED | `crawl_url` accepts `js_code: str | None` and `wait_for: str | None`; both are passed through `_build_run_config` to `CrawlerRunConfig`; wait_for docs specify "css:#element" or "js:() => condition" format |
| 3 | Claude can pass custom headers, cookies, user-agent, and timeout values per call and they are applied to that crawl only | VERIFIED | `headers` and `cookies` injected per-call via Playwright `before_goto`/`on_page_context_created` hooks in `_crawl_with_overrides` (lines 141-148); hooks cleared in `finally` block (lines 154-156) preventing leakage; `user_agent` and `page_timeout` passed directly to `CrawlerRunConfig` |
| 4 | Claude can control cache behavior (bypass, force-refresh, use-cached) and the tool respects the instruction | VERIFIED | `cache_mode` string param maps to CacheMode enum: enabled, bypass, disabled, read_only, write_only (lines 248-253); unknown values fall back to ENABLED with a warning log |
| 5 | Claude can specify CSS include/exclude selectors and only the matching content is returned | VERIFIED | `css_selector` and `excluded_selector` both accepted as `str | None` and passed directly to `CrawlerRunConfig(css_selector=..., excluded_selector=...)` (lines 111-112) |

**Score:** 5/5 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/crawl4ai_mcp/server.py` | `crawl_url` tool function | VERIFIED | Lines 175-278; decorated `@mcp.tool()`; 11 user-facing params + ctx |
| `src/crawl4ai_mcp/server.py` | `_build_run_config` helper with PruningContentFilter | VERIFIED | Lines 84-119; `PruningContentFilter(threshold=0.48)`; `verbose=False` enforced |
| `src/crawl4ai_mcp/server.py` | `_crawl_with_overrides` helper with hook-based injection | VERIFIED | Lines 122-155; `before_goto` and `on_page_context_created` hooks; try/finally cleanup |
| `README.md` | `crawl_url` documentation with parameter table | VERIFIED | 3 occurrences; tool table entry (line 80); Usage section with 11-param reference table (lines 90-103) |

All artifact levels:
- Level 1 (Exists): All files exist
- Level 2 (Substantive): No stubs — `crawl_url` is 103 lines with real implementation; helpers are 36 and 34 lines with real logic
- Level 3 (Wired): All artifacts connected (verified below)

### Key Link Verification (via Serena find_referencing_symbols)

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `crawl_url` | `_build_run_config` | direct call | WIRED | Serena confirms reference at line 259: `run_cfg = _build_run_config(...)` |
| `crawl_url` | `_crawl_with_overrides` | await | WIRED | Serena confirms reference at line 271: `result = await _crawl_with_overrides(app.crawler, url, run_cfg, headers, cookies)` |
| `_crawl_with_overrides` | `crawler.arun` | await | WIRED | Line 151: `return await crawler.arun(url=url, config=config)` |
| `crawl_url` | `_format_crawl_error` | return on failure | WIRED | Serena confirms reference at line 274: `return _format_crawl_error(url, result)` |
| `README.md` | `crawl_url` | tool table entry | WIRED | Line 80 table entry confirmed; Usage section added |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| CORE-01 | 02-01-PLAN.md | fit_markdown output with PruningContentFilter | SATISFIED | `_build_run_config` applies `PruningContentFilter(threshold=0.48)`; result extracted as `md.fit_markdown or md.raw_markdown` |
| CORE-02 | 02-01-PLAN.md | JS rendering control, js_code injection, wait_for | SATISFIED | `js_code` and `wait_for` params in `crawl_url`; passed to `CrawlerRunConfig` |
| CORE-03 | 02-01-PLAN.md | Custom headers, cookies, user-agent, timeout per call | SATISFIED | Headers/cookies injected via Playwright hooks with cleanup; `user_agent` and `page_timeout` in `CrawlerRunConfig` |
| CORE-04 | 02-01-PLAN.md | Cache behavior control | SATISFIED | `cache_mode` string-to-enum mapping covers all 5 CacheMode values |
| CORE-05 | 02-01-PLAN.md | CSS include/exclude selector scoping | SATISFIED | `css_selector` and `excluded_selector` both wired through to `CrawlerRunConfig` |

All 5 requirements satisfied. No orphaned requirements.

### Anti-Patterns Found

None detected.

- No `print()` calls in `src/crawl4ai_mcp/server.py` (ruff T201 passes)
- No TODO/FIXME/PLACEHOLDER comments in any modified file
- No stub patterns (`return null`, empty implementations, etc.)
- `verbose=False` appears at both required locations: BrowserConfig (line 48) and CrawlerRunConfig (line 119)
- ruff reports: "All checks passed!"

### Human Verification Required

1. **Live JS-heavy page crawl**
   - Test: Ask Claude to crawl a JS-rendered page (e.g., a React SPA) with `js_code` to scroll and `wait_for` a CSS selector
   - Expected: Returns meaningful markdown content rather than empty/skeleton HTML
   - Why human: Can't verify JS rendering quality programmatically without a live browser session

2. **Per-request cookie isolation**
   - Test: Make two sequential crawl calls — first with a cookie, second without — targeting a cookie-aware endpoint
   - Expected: Second call does not inherit cookies from the first
   - Why human: Requires a real service that reflects cookies back in the response to confirm isolation

3. **MCP tool registration (end-to-end Claude Code integration)**
   - Test: In an active Claude Code session with the MCP server running, type "ping the crawl4ai server then crawl https://example.com"
   - Expected: Claude invokes both `ping` and `crawl_url` tools and returns markdown content
   - Why human: MCP tool invocation from Claude Code UI cannot be verified programmatically

### Gaps Summary

No gaps. All 5 success criteria are fully implemented, wired, and confirmed by:
- Serena symbol-level reference tracing (all key links verified)
- Import verification: `from crawl4ai_mcp.server import crawl_url, _build_run_config, _crawl_with_overrides` succeeds
- MCP tool list via JSON-RPC: `['ping', 'crawl_url']` confirmed
- ruff lint: zero errors
- README: 3 crawl_url occurrences, full parameter table present

The only remaining verification items are human/runtime checks (JS rendering quality and cookie isolation) that cannot be confirmed statically.

---

_Verified: 2026-02-20T05:28:50Z_
_Verifier: Claude (gsd-verifier)_
