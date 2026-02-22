---
phase: quick
plan: 01
type: execute
wave: 1
depends_on: []
files_modified: []
autonomous: true
requirements: [DIAG-01]
must_haves:
  truths:
    - "User sees all unit test results with pass/fail counts"
    - "User sees live MCP tool responses from real URLs"
    - "User gets a clear summary of server health and capability"
  artifacts: []
  key_links: []
---

<objective>
Run the full test suite and exercise live MCP tools against real websites, presenting all results to the user.

Purpose: Give the user full visibility into test health and live server capability.
Output: Displayed test results + live crawl output (no files created).
</objective>

<execution_context>
@/Users/brianpotter/.claude/get-shit-done/workflows/execute-plan.md
</execution_context>

<context>
@CLAUDE.md
Test files (8 total):
- tests/test_profiles.py (ProfileManager, build_run_config, merge order, verbose enforcement)
- tests/test_extraction.py (extract_structured registration, _check_api_key, PROVIDER_ENV_VARS)
- tests/test_extraction_css.py (extract_css registration, EXTR-03 enforcement)
- tests/test_crawl_many.py (crawl_many registration, _format_multi_results)
- tests/test_deep_crawl.py (deep_crawl registration, BFS strategy, FilterChain, scope mapping)
- tests/test_crawl_sitemap.py (crawl_sitemap registration, _fetch_sitemap_urls parsing)
- tests/test_sessions.py (AppContext.sessions, session tracking, destroy flow)
- tests/test_update.py (check_update, _fetch_changelog_summary, _startup_version_check)
</context>

<tasks>

<task type="auto">
  <name>Task 1: Run unit test suite and show results</name>
  <files></files>
  <action>
Run `uv run pytest -v` from the project root `/Users/brianpotter/ai_tools/crawl4ai_mcp` and OUTPUT the full results to the user. Also run `uv run ruff check src/` to confirm no lint issues (especially T201 print() violations).

Present the results clearly:
- Total tests, passed, failed, errors
- Any failures with details
- Lint status (clean or issues found)
  </action>
  <verify>pytest exits with code 0, ruff check exits with code 0</verify>
  <done>User can see full pytest verbose output and ruff lint results</done>
</task>

<task type="auto">
  <name>Task 2: Live MCP tool test with real URLs</name>
  <files></files>
  <action>
Exercise the live crawl4ai MCP server tools against real test sites. Run each of the following and OUTPUT all results to the user:

1. **ping** -- Call `mcp__crawl4ai__ping` to verify server health
2. **list_profiles** -- Call `mcp__crawl4ai__list_profiles` to show available profiles
3. **crawl_url (static)** -- Call `mcp__crawl4ai__crawl_url` with:
   - url: `https://webscraper.io/test-sites/e-commerce/static/computers/laptops`
   - cache_mode: "bypass"
   Show the returned markdown content (first ~500 chars to confirm extraction works)
4. **extract_css** -- Call `mcp__crawl4ai__extract_css` with:
   - url: `https://webscraper.io/test-sites/e-commerce/static/computers/laptops`
   - schema: `{"name": "Laptops", "baseSelector": "div.thumbnail", "fields": [{"name": "title", "selector": "a.title", "type": "text"}, {"name": "price", "selector": "h4.price", "type": "text"}]}`
   - cache_mode: default (use cached from prior crawl)
   Show full JSON extraction results
5. **list_sessions** -- Call `mcp__crawl4ai__list_sessions` to show session state
6. **check_update** -- Call `mcp__crawl4ai__check_update` to show version status

Do NOT call extract_structured (costs real LLM tokens) or deep_crawl/crawl_sitemap (too slow for diagnostic). Do NOT call crawl_many (unnecessary for diagnostic -- crawl_url proves the engine works).

For each tool call, OUTPUT the full response so the user sees exactly what the MCP server returns.
  </action>
  <verify>All 6 tool calls return without error. ping returns "ok". crawl_url returns markdown content. extract_css returns JSON with laptop data.</verify>
  <done>User can see live responses from ping, list_profiles, crawl_url, extract_css, list_sessions, and check_update</done>
</task>

<task type="auto">
  <name>Task 3: Present summary to user</name>
  <files></files>
  <action>
Compile a clear summary table for the user showing:

**Unit Tests:**
- Total test count and pass/fail
- Test file breakdown (tests per file)
- Any failures or warnings

**Live MCP Tools (12 registered):**
| Tool | Status | Notes |
|------|--------|-------|
| ping | [result] | Server health |
| list_profiles | [result] | Profile count |
| crawl_url | [result] | Content extracted? |
| extract_css | [result] | JSON items count |
| extract_structured | SKIPPED | Costs LLM tokens |
| crawl_many | SKIPPED | Not needed for diagnostic |
| deep_crawl | SKIPPED | Too slow for diagnostic |
| crawl_sitemap | SKIPPED | Too slow for diagnostic |
| create_session | SKIPPED | Tested via unit tests |
| destroy_session | SKIPPED | Tested via unit tests |
| list_sessions | [result] | Session count |
| check_update | [result] | Version status |

**Lint:** Clean or issues

**Overall verdict:** HEALTHY / DEGRADED / BROKEN
  </action>
  <verify>Summary is presented clearly to the user</verify>
  <done>User has a complete picture of test suite health and live MCP server capability</done>
</task>

</tasks>

<verification>
- pytest passes all tests (exit code 0)
- ruff check passes (exit code 0)
- ping returns "ok"
- crawl_url returns markdown from a real page
- extract_css returns structured JSON with laptop data
- Summary table presented to user
</verification>

<success_criteria>
User can see: (1) all unit test results verbose, (2) live MCP tool responses from real URLs, (3) a summary table with overall health verdict.
</success_criteria>

<output>
No SUMMARY file needed -- this is a diagnostic/display task. All output goes directly to the user.
</output>
