# Project Research Summary

**Project:** crawl4ai MCP Server
**Domain:** Python MCP Server wrapping crawl4ai for Claude Code
**Researched:** 2026-02-19
**Confidence:** HIGH

## Executive Summary

This project is a local Python MCP server that exposes crawl4ai's web crawling and extraction capabilities as tools for Claude Code. Experts build this type of server using the official MCP Python SDK's `FastMCP` class with stdio transport, wrapping a long-lived `AsyncWebCrawler` singleton managed via the lifespan pattern. The core architectural insight is that a warm browser process (kept alive across requests) is essential for acceptable performance, and that tools must be focused and well-described to be reliably invoked by Claude.

The recommended approach is to build in deliberate layers: foundation first (server scaffolding, browser lifecycle, stdio hygiene), then the core crawl tool, then a profile system before adding more tools, then extraction and batch capabilities. This order is dictated by hard dependencies: every subsequent tool depends on the crawler singleton and profile system being correct. The differentiated features -- preset profiles for common configurations, true parallel batch crawling via `arun_many()`, and separate focused extraction tools -- require that foundation to be solid before adding them.

The primary risks are browser memory leaks (a confirmed crawl4ai issue through v0.7.x), stdout corruption breaking the MCP transport, and LLM extraction cost blowup. All three can be fully mitigated at design time: the lifespan singleton pattern addresses leaks, strict stderr-only logging prevents stdout pollution, and making LLM extraction opt-in with a separate dedicated tool prevents cost surprises. None of these require heroic engineering -- they require discipline from the first line of code.

## Key Findings

### Recommended Stack

The stack is clear and has no meaningful alternatives worth considering. Python 3.12+ is the target runtime (both `mcp` and `crawl4ai` require 3.10+, but 3.12 provides better asyncio performance). The `uv` package manager is essential -- it provides automatic venv management, a lockfile, and enables Claude Code to invoke the server via `uv run --directory /path/to/project` from any working directory. Playwright's Chromium backend is bundled with crawl4ai and should be installed via `crawl4ai-setup`, not the global `playwright install` command.

**Core technologies:**
- Python 3.12+: Runtime — best asyncio performance, required by both key dependencies
- `mcp` 1.26.0 (FastMCP): MCP server framework — official Anthropic SDK, stdio transport by default, decorator-based tool registration
- `crawl4ai` 0.8.0: Web crawling engine — the core capability being wrapped; includes JS rendering, LLM extraction, deep crawl, batch crawl
- `uv` 0.10.x: Dependency management and runner — enables `uv run` invocation from Claude Code, manages the venv automatically
- Playwright Chromium (bundled via crawl4ai): Browser automation — headless JS rendering, installed via `crawl4ai-setup`

**What to avoid:** `crawl4ai[all]` or `crawl4ai[torch]` extras (adds 2GB+ of unnecessary ML dependencies), `crawl4ai[sync]` (deprecated Selenium mode), SSE or HTTP transport (unnecessary complexity for local dev), and `asyncio.run()` wrapping `mcp.run()` (FastMCP manages the event loop itself).

### Expected Features

The feature landscape splits into three tiers. The highest-value V1 features are the `crawl` tool (single URL to markdown), `crawl_many` (parallel batch via `arun_many()`), and preset profiles (`fast`, `js-heavy`, `stealth`) -- this last one is a genuine differentiator since no existing crawl4ai MCP server offers named presets. V1.x adds structured extraction (`extract_structured` with LLM, `extract_css` without), `deep_crawl` (BFS link-following), and content filtering (`fit_markdown`). V2+ defers sitemap crawling, advanced filter/scorer combos, and cross-call session persistence.

**Must have (table stakes):**
- Single URL crawl to markdown — every competitor has this; absence makes the server useless
- JS rendering via Playwright — required for SPAs and modern sites
- CSS selector scoping and tag exclusion — necessary to avoid nav/footer noise
- Error handling (success, status_code, error_message) — required for Claude to handle failures
- Cache control, timeout, link extraction, metadata extraction — all low-cost, high-value basics
- Robots.txt compliance (`check_robots_txt=True`) — ethical default, should be enabled out of the box

**Should have (competitive):**
- Preset profiles (`fast`, `js-heavy`, `stealth`) — the single biggest DX differentiator over existing MCP servers
- `crawl_many` parallel batch — existing servers crawl sequentially; `arun_many()` provides real concurrency
- `extract_structured` (LLM) and `extract_css` (deterministic) as separate tools — cleaner than lumping both into one tool
- `deep_crawl` (BFS) with hard limits — essential for documentation ingestion workflows
- Content filtering via `fit_markdown` — produces cleaner LLM-consumable output than any competitor
- `check_updates` tool — maintenance convenience, no existing server offers this

**Defer (v2+):**
- Sitemap-based crawling — approximated by `deep_crawl`; adds complexity for niche use
- Deep crawl with filters/scorers (FilterChain, CompositeScorer) — complex config surface, `max_depth` + `max_pages` covers 80% of the need
- Cross-call session persistence for multi-step auth workflows — requires stateful MCP server design; complexity not justified at launch

**Anti-features to explicitly avoid:** Full hook system exposure (arbitrary code execution risk), exposing all 40+ crawl4ai config params as tool args (overwhelms LLM schema parsing), in-process pip upgrades, and Docker/separate-process architecture for what should be a direct library dependency.

### Architecture Approach

The architecture centers on a single `AsyncWebCrawler` instance created at server startup via FastMCP's lifespan async context manager and shared across all tool calls via `AppContext`. A Profile Manager loads YAML files at startup and merges them with per-call overrides via a Config Builder layer. Tools are organized by domain into focused modules (`crawl.py`, `extract.py`, `batch.py`, `admin.py`). An abstraction layer between tool handlers and crawl4ai internals protects against the library's frequent breaking API changes.

**Major components:**
1. FastMCP Server (`server.py`) — protocol handling, tool registration, lifespan management, stdio transport
2. Crawler Manager (`crawler.py`) — `AsyncWebCrawler` singleton lifecycle (start at boot, close at shutdown)
3. Profile Manager (`profiles.py`) — load YAML profiles, merge with per-call overrides
4. Config Builder (`config_builder.py`) — pure functions translating merged config dicts to `BrowserConfig` + `CrawlerRunConfig` objects
5. Tool Modules (`tools/`) — focused async functions grouped by domain, registered via `@mcp.tool()` decorators
6. Admin Tools (`tools/admin.py`) — `check_update` (reports available update, never performs it), `list_profiles`, `server_info`
7. Profiles (`profiles/*.yaml`) — editable without code changes; default, fast, js_heavy, stealth configurations
8. Update Script (`scripts/update.sh`) — offline-only `pip upgrade` + `playwright install`; never run in-process

### Critical Pitfalls

1. **Browser process leaks (memory exhaustion)** — Use the FastMCP lifespan singleton pattern for `AsyncWebCrawler`. Never create a new crawler per request. Set `--disable-gpu`, `--disable-dev-shm-usage`, `--no-sandbox` in BrowserConfig args. Explicitly clear large result fields after use. Limit concurrent crawls to 2-3.

2. **stdout corruption breaking MCP transport** — Route ALL output to stderr (`logging.basicConfig(stream=sys.stderr)`). Never use `print()` anywhere in server code. Set `verbose=False` on all crawl4ai configs. Use `ctx.info()` / `ctx.debug()` for server-side logging. Enforce with a pre-commit lint rule.

3. **LLM extraction cost blowup** — Make LLM extraction opt-in via a separate `extract_structured` tool with a cost warning in its description. Default all crawl tools to markdown output. Use `input_format="fit_markdown"` (reduces tokens 60-80%). Never combine LLM extraction with deep crawl. Prefer `JsonCssExtractionStrategy` for deterministic content.

4. **Deep crawl runaway** — Enforce hard `max_depth` (cap at 3) and `max_pages` (cap at 50) at the tool layer regardless of user input. Implement a tool-level timeout (120s max). Use `include_external=False` by default. Prefer BFS over DFS.

5. **crawl4ai breaking API changes** — Pin the exact version (`crawl4ai==0.8.0`) in `pyproject.toml`. Build an abstraction layer so tool handlers never call crawl4ai directly. Write integration tests for every crawl4ai API used. Monitor CHANGELOG.md for entries tagged "breaking."

## Implications for Roadmap

The architecture research explicitly defines a build order based on hard dependencies. The roadmap should follow it closely.

### Phase 1: Foundation

**Rationale:** Server scaffolding, browser lifecycle, and logging hygiene must be correct from day one. The five pitfalls that must be addressed in Phase 1 (browser leaks, stdout corruption, API abstraction, tool description quality, URL validation/SSRF) cannot be retrofitted after other tools are built on top of a broken foundation.

**Delivers:** A functioning MCP server registered with Claude Code that starts without errors, runs the lifespan, and connects via stdio. No crawl tools yet, but the plumbing is correct.

**Addresses:** FastMCP setup, `uv` project initialization, Playwright browser installation via `crawl4ai-setup`, Claude Code MCP registration, stderr-only logging enforcement, `AppContext` with `AsyncWebCrawler` singleton.

**Avoids:** stdout corruption (linting from day one), browser process leaks (lifespan pattern from day one), crawl4ai API instability (abstraction layer from day one).

### Phase 2: Core Crawl Tool

**Rationale:** The `crawl` tool is the atomic operation everything else builds on. It must be solid before building the profile system or any other tools. Validating the full request/response cycle early surfaces transport, serialization, and crawl4ai integration issues cheaply.

**Delivers:** A working `crawl` tool that Claude Code can invoke to fetch a URL and receive clean markdown back. Includes CSS selector scoping, tag exclusion, cache control, timeout, and proper error surfacing.

**Addresses:** Table stakes features (JS rendering, CSS selector, error handling, link extraction, metadata). P1 features from the feature matrix.

**Avoids:** Content size overwhelming Claude (truncation to ~50KB max), crawl4ai verbose mode polluting stdout.

### Phase 3: Profile System

**Rationale:** Profiles must be built before adding more tools. Every subsequent tool accepts a `profile` parameter. Getting the merge logic (default <- named profile <- per-call overrides) right before writing the next five tools prevents duplicated and inconsistent config logic.

**Delivers:** `ProfileManager` with YAML loading, the Config Builder merge logic, and the three core profiles (`fast`, `js-heavy`, `stealth`). The `crawl` tool gains `profile` support. No new tools yet.

**Addresses:** Preset profiles as a key differentiator. The architecture pattern of profile + override merging. Tool description quality (profiles simplify parameter choices dramatically).

**Avoids:** Anti-pattern 3 (passing all config params as tool args). Keeps each tool at 3-5 parameters.

### Phase 4: Extraction Tools

**Rationale:** Extraction tools depend on the crawler singleton and profile system being stable. LLM extraction is the flagship "killer feature" but also the primary cost risk -- it must be implemented as a separate, clearly-marked tool with explicit cost warnings.

**Delivers:** `extract_structured` tool (LLM-powered, opt-in, with cost warnings) and `extract_css` tool (deterministic, free, fast). Both use `AppContext.crawler` via the lifespan.

**Addresses:** The LLM extraction cost pitfall (separate tool, opt-in). P2 features: LLM structured extraction, CSS/JSON extraction, content filtering (`fit_markdown`).

**Avoids:** LLM extraction as a default code path, combining LLM extraction with bulk crawls, token cost blowup.

### Phase 5: Batch and Deep Crawl

**Rationale:** `crawl_many` and `deep_crawl` build on the same crawler singleton and profile system. They introduce concurrency (via `arun_many()` and `MemoryAdaptiveDispatcher`) and the deep crawl runaway risk. These need the profile and config systems to be solid before safe concurrency limits and URL filtering can be layered in.

**Delivers:** `crawl_many` tool (parallel batch with `SemaphoreDispatcher`/`MemoryAdaptiveDispatcher`) and `deep_crawl` tool (BFS with hard `max_depth` and `max_pages` caps, tool-level timeout).

**Addresses:** True parallel batch crawling as a differentiator. Deep crawl for documentation ingestion.

**Avoids:** Deep crawl runaway (hard limits at tool layer), memory exhaustion (concurrency caps, `MemoryAdaptiveDispatcher`).

### Phase 6: Admin and Polish

**Rationale:** Admin tools (`check_update`, `list_profiles`, `server_info`) and the offline update script add maintenance utility. These are independent of crawling functionality and are low-risk, so they come last.

**Delivers:** `check_update` (reports version delta, returns shell command, never executes in-process), `list_profiles`, `server_info`, `scripts/update.sh` (offline upgrade + Playwright reinstall).

**Addresses:** Update management differentiator. The offline update anti-pattern (Pattern 3 from architecture research). Maintenance convenience.

**Avoids:** In-process pip upgrade (catastrophic for a running Python process).

### Phase Ordering Rationale

- Phases 1-3 are strictly ordered by dependency: foundation before crawl tool before profiles. No parallelism possible.
- Phases 4-5 are somewhat parallel after Phase 3 is solid, but extraction tools are simpler than batch tools, so 4 before 5 reduces risk.
- Phase 6 is fully independent and can be done any time after Phase 2, but is deferred since it delivers maintenance value, not user-facing capability.
- The pitfall-to-phase mapping from PITFALLS.md confirms this ordering: all five "Phase 1" pitfalls (leaks, stdout, API instability, tool descriptions, URL validation) must be resolved before Phases 2+.

### Research Flags

Phases likely needing deeper research during planning:
- **Phase 4 (Extraction):** LLM extraction cost modeling and `fit_markdown` behavior under different content types may need a focused research spike before committing to the tool's cost warning language and default parameters.
- **Phase 5 (Batch/Deep):** `MemoryAdaptiveDispatcher` vs `SemaphoreDispatcher` trade-offs for local single-user use may need a benchmarking spike; the research identifies memory as the first bottleneck but does not quantify per-URL overhead precisely.

Phases with standard patterns (skip research-phase):
- **Phase 1 (Foundation):** FastMCP lifespan, stdio transport, `uv` project setup -- all well-documented with high-confidence sources. Follow the patterns directly.
- **Phase 2 (Core Crawl):** `AsyncWebCrawler.arun()` with `BrowserConfig` + `CrawlerRunConfig` is the canonical crawl4ai usage pattern. Straightforward implementation.
- **Phase 3 (Profiles):** YAML loading + dict merging is standard Python. The profile schema is well-defined by the research.
- **Phase 6 (Admin):** PyPI JSON API query + `importlib.metadata` version check is trivial. Offline update script follows the documented pattern.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | Both primary sources (Context7 MCP SDK docs, Context7 crawl4ai docs) are official documentation with high benchmark scores. PyPI package versions confirmed. |
| Features | HIGH | crawl4ai official docs, three existing open-source MCP server implementations, and MCP specification all used as cross-references. Competitor analysis grounds the differentiator claims. |
| Architecture | HIGH | FastMCP lifespan pattern is documented in the SDK README with explicit examples. Anti-patterns are grounded in crawl4ai GitHub issues and confirmed community behavior. |
| Pitfalls | HIGH | Memory leak pitfalls grounded in specific GitHub issue numbers (#1256, #1608, #361, #943). stdout corruption grounded in Roo Code GitHub issue #5462 and MCP debugging docs. LLM cost analysis backed by crawl4ai cost memo and GitHub issue #712. |

**Overall confidence:** HIGH

### Gaps to Address

- **Exact memory per Chromium tab:** The research recommends limiting to 2-3 concurrent crawls but the per-tab memory footprint on macOS with `text_mode=True` vs default is not precisely quantified. Validate during Phase 5 implementation with `ps` monitoring.
- **`MemoryAdaptiveDispatcher` behavior for single-user local use:** The dispatcher monitors system memory, not process memory. On a developer machine with other apps running, the adaptive logic may be overly conservative. Consider `SemaphoreDispatcher` as the default with a small fixed permit count (e.g., 3) and document `MemoryAdaptiveDispatcher` as an alternative.
- **crawl4ai 0.8.0 deep crawl API stability:** The research notes that crawl4ai's BFS deep crawl is a v0.8.0 feature. The exact API (strategy config, filter integration) should be validated against the live 0.8.0 docs before Phase 5 implementation, given the library's history of breaking changes.
- **MCP tool response size limits:** Claude Code's context window impact of large markdown responses is noted but the actual token limit for MCP tool responses is not confirmed. The 50KB truncation recommendation is a heuristic. Validate empirically during Phase 2.

## Sources

### Primary (HIGH confidence)
- Context7 `/modelcontextprotocol/python-sdk` (Benchmark 86.8) — FastMCP API, lifespan pattern, tool decorators, stdio transport
- Context7 `/websites/crawl4ai` (Benchmark 90.7) — AsyncWebCrawler usage, BrowserConfig, CrawlerRunConfig, extraction strategies
- PyPI `mcp` 1.26.0 — version, dependencies confirmed
- PyPI `crawl4ai` 0.8.0 — version, extras, dependencies confirmed
- crawl4ai Official Documentation (docs.crawl4ai.com) — deep crawling, LLM strategies, session management
- MCP Official Documentation (modelcontextprotocol.io) — stdio transport, server registration, tool specification
- crawl4ai GitHub Issues #1256, #1608, #361, #943 — browser memory leak confirmation
- Official uv docs (docs.astral.sh/uv) — project initialization, `uv add`, `uv sync`, `uv run`

### Secondary (MEDIUM confidence)
- sadiuysal/crawl4ai-mcp-server (GitHub) — competitor feature baseline
- BjornMelin/crawl4ai-mcp-server (GitHub) — competitor feature baseline
- Apify crawl4ai MCP server — competitor feature baseline
- Roo Code GitHub Issue #5462 — stdout corruption in stdio MCP servers
- crawl4ai CHANGELOG.md — breaking change history, API migration patterns
- Klavis AI: "Less is More: MCP Design Patterns for Agents" — tool parameter design guidance
- Anthropic Engineering: "Writing Tools for Agents" — tool description best practices
- Perplexity research — MCP server architecture patterns, safe update mechanisms

### Tertiary (LOW confidence)
- crawl4ai cost memo (memo.d.foundation) — LLM extraction cost estimates ($0.001-$0.01/page); validate empirically
- Perplexity on MemoryAdaptiveDispatcher vs SemaphoreDispatcher trade-offs — verify against actual v0.8.0 behavior

---
*Research completed: 2026-02-19*
*Ready for roadmap: yes*
