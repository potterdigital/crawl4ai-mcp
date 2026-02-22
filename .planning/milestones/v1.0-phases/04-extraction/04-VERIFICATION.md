---
phase: 04-extraction
verified: 2026-02-20T14:23:08Z
status: passed
score: 9/9 must-haves verified
gaps: []
human_verification:
  - test: "Call extract_structured with a real OpenAI key set in env"
    expected: "Returns valid JSON matching the provided schema, followed by LLM Usage section"
    why_human: "LLM call requires live API key and live URL — cannot verify in unit tests"
  - test: "Call extract_css on a known CSS-structured page (e.g. webscraper.io/test-sites/e-commerce/static)"
    expected: "Returns JSON array of extracted items with no LLM call — confirmed by absence of usage section"
    why_human: "Full crawl + CSS extraction requires live browser and live URL"
---

# Phase 4: Extraction Verification Report

**Phase Goal:** Claude can extract structured JSON from any page using either LLM-powered extraction (opt-in, with cost warning) or deterministic CSS/selector extraction (free, no LLM)
**Verified:** 2026-02-20T14:23:08Z
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Claude can call `extract_structured` with a JSON schema + instruction and receive typed JSON | VERIFIED | Function exists at server.py:348, decorated `@mcp.tool()`, registered in FastMCP tool manager (test_tool_registered passes) |
| 2 | `extract_structured` docstring prominently warns about LLM API cost | VERIFIED | Lines 361-364: "WARNING: This tool calls an external LLM API and incurs token costs. Each call may cost $0.01-$1+ depending on page size and model. Use extract_css for cost-free deterministic extraction when possible." |
| 3 | Claude can call `extract_css` with a baseSelector and field definitions and receive structured JSON with no LLM call | VERIFIED | Function exists at server.py:442, uses `JsonCssExtractionStrategy` only — no LLM imports, no provider param, no LLMConfig usage |
| 4 | LLM API keys are sourced from server-side environment variables only | VERIFIED | `_check_api_key` at line 112 reads `os.environ.get(env_var)`. No `api_key`, `api_token`, or credential parameter in any tool signature |
| 5 | `crawl_url` never triggers LLM extraction — `extract_structured` is a separate entry point | VERIFIED | `crawl_url` signature (lines 225-238) has no `schema`, `instruction`, `provider`, or `extraction_strategy` params. Test `test_crawl_url_has_no_extraction_strategy_param` and `test_crawl_url_has_no_schema_param` pass |
| 6 | Missing API key env var returns a clear structured error before LLM call | VERIFIED | `_check_api_key` is called before any network activity at line 386; returns structured error string with env var name and export command |
| 7 | `verbose=False` on both `LLMExtractionStrategy` and `CrawlerRunConfig` in `extract_structured` | VERIFIED | Lines 399, 407: `verbose=False` on strategy at construction; `verbose=False` on CrawlerRunConfig. MCP transport protected |
| 8 | `verbose=False` on both `JsonCssExtractionStrategy` and `CrawlerRunConfig` in `extract_css` | VERIFIED | Lines 500, 507: `verbose=False` on strategy; `verbose=False` on CrawlerRunConfig |
| 9 | Token usage reported without calling `strategy.show_usage()` | VERIFIED | Line 431: `usage = strategy.total_usage` — attribute access only. `show_usage` appears only in a comment (line 430), never called |

**Score:** 9/9 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/crawl4ai_mcp/server.py` | `extract_structured` tool with `@mcp.tool()` | VERIFIED | Lines 348-438, decorator present, 91 lines of substantive implementation |
| `src/crawl4ai_mcp/server.py` | `_check_api_key` helper | VERIFIED | Lines 112-131, validates provider prefix against `PROVIDER_ENV_VARS`, returns structured error or None |
| `src/crawl4ai_mcp/server.py` | `PROVIDER_ENV_VARS` mapping | VERIFIED | Lines 87-94, maps openai/anthropic/gemini/deepseek/groq to env var names, ollama to None |
| `src/crawl4ai_mcp/server.py` | `extract_css` tool with `@mcp.tool()` | VERIFIED | Lines 442-530, decorator present, 89 lines of substantive implementation |
| `tests/test_extraction.py` | Unit tests for `_check_api_key`, `PROVIDER_ENV_VARS`, `extract_structured` registration | VERIFIED | 9 tests, all passing |
| `tests/test_extraction_css.py` | Unit tests for `extract_css` registration and EXTR-03 enforcement | VERIFIED | 6 tests, all passing |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `extract_structured` | `crawl4ai.LLMExtractionStrategy` | `strategy = LLMExtractionStrategy(llm_config=llm_config, ...)` | WIRED | Lines 393-400: `LLMExtractionStrategy` constructed with `llm_config`, `schema`, `extraction_type`, `instruction`, `input_format`, `verbose=False` |
| `extract_structured` | `AppContext.crawler` | `ctx.request_context.lifespan_context` | WIRED | Line 416: `app: AppContext = ctx.request_context.lifespan_context`; line 417: `result = await _crawl_with_overrides(app.crawler, url, run_cfg)` |
| `extract_css` | `crawl4ai.JsonCssExtractionStrategy` | `strategy = JsonCssExtractionStrategy(schema, verbose=False)` | WIRED | Line 500: `JsonCssExtractionStrategy(schema, verbose=False)` with extraction strategy passed to CrawlerRunConfig at line 505 |
| `extract_css` | `AppContext.crawler` | `ctx.request_context.lifespan_context` | WIRED | Line 516: `app: AppContext = ctx.request_context.lifespan_context`; line 517: `result = await _crawl_with_overrides(app.crawler, url, run_cfg)` |
| `_check_api_key` | `os.environ` | `os.environ.get(env_var)` | WIRED | Line 124: reads env var by name, never reads API key values into parameters or logs |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| EXTR-01 | 04-01-PLAN.md | User can extract structured JSON using LLM (schema + instruction + cost warning) | SATISFIED | `extract_structured` tool exists, registered, docstring contains cost warning mentioning `extract_css` alternative |
| EXTR-02 | 04-02-PLAN.md | User can extract structured JSON using CSS selectors (deterministic, no LLM cost) | SATISFIED | `extract_css` tool exists, registered, uses `JsonCssExtractionStrategy` only, no LLM imports or params |
| EXTR-03 | 04-01-PLAN.md, 04-02-PLAN.md | LLM extraction requires explicit opt-in — never triggered by `crawl_url` | SATISFIED | `crawl_url` params verified to contain no `schema`, `extraction_strategy`, `provider`, or `instruction` — confirmed by 2 passing unit tests |
| EXTR-04 | 04-01-PLAN.md | LLM API keys read from server-side env vars, never passed as tool parameters | SATISFIED | `_check_api_key` reads from `os.environ`; no `api_key`/`api_token` param in any tool signature; `LLMConfig(provider=provider)` auto-resolves key |

### Anti-Patterns Found

| File | Pattern | Severity | Impact |
|------|---------|----------|--------|
| None | — | — | — |

No TODO, FIXME, placeholder, or `print()` calls found in `src/`. The one occurrence of `print()` text at line 430 of `server.py` is inside a comment: `# NEVER call strategy.show_usage() (uses print())`.

Ruff lint: clean (all checks passed, no T201 violations).

### Human Verification Required

#### 1. LLM Extraction End-to-End

**Test:** With `OPENAI_API_KEY` set, call `extract_structured` on `https://webscraper.io/test-sites/e-commerce/static/computers/laptops` with a schema for product name and price.
**Expected:** Returns a JSON array of laptop objects with name and price fields, followed by `--- LLM Usage ---` section showing token counts.
**Why human:** Requires live API key and live network — cannot mock in unit tests without integration test infrastructure.

#### 2. CSS Extraction End-to-End

**Test:** Call `extract_css` on `https://webscraper.io/test-sites/e-commerce/static/computers/laptops` with `baseSelector: "div.product-wrapper"` and fields for title and price.
**Expected:** Returns a JSON array of items. No `--- LLM Usage ---` section appears. No API call is made (no API key needed).
**Why human:** Requires live browser crawl — CSS selectors need to be validated against actual rendered HTML.

#### 3. Missing API Key Error Path

**Test:** With `OPENAI_API_KEY` unset, call `extract_structured` with `provider: "openai/gpt-4o-mini"`.
**Expected:** Returns structured error message containing "OPENAI_API_KEY", "not set", and the export command — without any network call to OpenAI.
**Why human:** Verifying that the error fires before any network activity requires observing server behavior in the live MCP session.

### Gaps Summary

None. All 9 observable truths are verified. All 4 requirement IDs (EXTR-01, EXTR-02, EXTR-03, EXTR-04) are satisfied. Both tools are substantive implementations with real crawl4ai strategy integration, proper `verbose=False` enforcement, and meaningful error handling. 48 tests pass with zero failures.

---

_Verified: 2026-02-20T14:23:08Z_
_Verifier: Claude (gsd-verifier)_
