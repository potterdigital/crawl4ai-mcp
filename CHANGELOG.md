# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [2.0.0] - 2026-08-16

### Breaking

- **The multi-page crawl and CSS extraction tools now return structured data instead of a formatted string.** `crawl_many`, `crawl_sitemap`, and `deep_crawl` return a `CrawlBatchResult`; `extract_css` returns an `ExtractionResult`. Each declares an `outputSchema` and populates `structuredContent`, so a client can sort, filter, and validate results instead of parsing prose. `crawl_url` is deliberately unchanged and still returns plain markdown: a single page has no tabular structure to expose, and converting it would only bury the content in JSON escaping.

  `CrawlBatchResult` carries `crawled`, `total`, `pages[]`, and optional `output_dir`, `manifest`, and `note`. Each page carries `url`, `success`, and then `markdown` or `error`, plus `depth` / `parent_url` for `deep_crawl` and `file` when `output_dir` was used. Successes sort ahead of failures so the useful half of a partial crawl is not buried.

  `extract_css` also now returns its records **parsed**, in `items`, rather than as a JSON string the caller had to decode a second time. Failures that used to come back as a human-readable error string are now reported in the `error` field with `count: 0`, so the URL and the reason survive.

  Migrating: read `structuredContent`, or parse the text block as JSON. The text block is the same data, pretty-printed. Anything matching on the old `Crawled N of M URLs successfully.` / `## Failed URLs` prose needs updating.

### Fixed

- **Long crawls are no longer aborted for idleness.** `crawl_many`, `crawl_sitemap`, and `deep_crawl` emitted nothing between the tool call and its response, so a crawl that ran longer than the client's idle window was killed outright and its work lost. Clients abort a tool call that sends neither a response nor a progress notification for that window; in Claude Code it is 30 minutes on stdio, and stdio stopped being exempt in 2.1.203. This was reproduced against a real client, not inferred: with the window shrunk for the test, a batch crawl died with `sent no response or progress for 30s; aborting`. It is reachable in normal use — 500 sitemap URLs at concurrency 10 against a site that hits the 60s page timeout is roughly 50 minutes. `deep_crawl` now streams and reports each completed page; `crawl_many` and `crawl_sitemap` heartbeat every 15 seconds. Per-page progress for those two would require swapping `SemaphoreDispatcher` for `MemoryAdaptiveDispatcher`, the only dispatcher crawl4ai ships that streams, which stalls dispatch above a system-memory threshold; that is not a failure mode worth adding to everyone's crawls for a nicer progress message.
- **`repair_browser` no longer races the same abort.** Its install timeout is 1800 seconds, exactly the default stdio idle window, so a slow ~150MB Chromium download could lose to the client's abort. It now heartbeats while installing.

### Added

- **MCP tool annotations on all 13 tools**, plus display titles. Previously every tool declared none, so clients saw the spec defaults — destructive and open-world — for all of them, including `ping` and `list_profiles`. Notably, the crawl and extract tools are **not** marked read-only: each accepts `js_code`, which executes caller-supplied JavaScript in the live page, and a client that skips confirmation for read-only tools would be auto-approving arbitrary script execution against any URL, including inside an authenticated session created by `create_session`. They are marked non-destructive instead, which carries the useful signal. `destroy_session` is the only tool marked destructive.

### Changed

- **Migrated to MCP Python SDK 2.0.0**, which adds protocol revision `2026-07-28`. The pin moves from `>=1.26.0,<2.0.0` to `>=2.0.0,<3.0.0`: 1.x cannot import `mcp.server.mcpserver`, so this server no longer runs on it. `FastMCP` is now `MCPServer` (`mcp.server.fastmcp` → `mcp.server.mcpserver`), and `Context` dropped its session type parameter, so `Context[ServerSession, AppContext]` is now `Context[AppContext]`. Note that `2026-07-28` is not reachable through the `initialize` handshake — it belongs to the stateless per-request era reached via `server/discover`, and `2025-11-25` remains the newest handshake-negotiable revision. A client using the classic handshake therefore still negotiates `2025-11-25`, which is correct, and the server now additionally answers `server/discover`, `tools/list`, and `tools/call` on `2026-07-28`.
- **Documented that `output_dir` overwrites.** The batch crawl tools write per-page `.md` files and a `manifest.json` into the directory you name, replacing any same-named files without warning. That behavior was always there and undocumented.

## [1.2.0] - 2026-08-16

### Changed

- **A missing browser no longer kills the server.** The startup preflight used to `sys.exit(1)` on a missing or stale Chromium build. That produced a clean stderr message nobody saw: exiting before the stdio transport opens means the MCP client reports only "failed to connect", with the remediation line buried in a connect log. The server now starts regardless and reports the condition through `ping` and every crawl tool, where an agent actually reads it. Preflight still warns on stderr.
- **Upgraded crawl4ai** from 0.8.6 to 0.9.2. The 0.9.0 breaking changes are confined to the Docker API server (auth required, loopback bind, request trust boundary); upstream states the in-process pip library is unchanged, which is what this server uses. Picks up the 0.8.8/0.8.9/0.9.0 security fixes (SSRF, path traversal, arbitrary file write, credential exfiltration).
- **Bounded the `mcp` dependency below 2.0.0.** The spec was open-ended, so a routine `uv lock --upgrade` could install mcp 2.0.0 — which renames `FastMCP` to `MCPServer` and drops `MCP_*` env var support, breaking this server's imports.

### Added

- **Automatic browser repair.** When the browser is unavailable at startup, the server installs it in the background and brings the crawler up without a restart. Runs in the background rather than during startup deliberately: `MCP_TIMEOUT` bounds server startup (Anthropic's documented example is 10 seconds) while tool calls get a far longer budget, so a ~150MB Chromium download during the handshake would recreate the very connect failure this removes. Disable with `CRAWL4AI_MCP_AUTO_REPAIR=0`.
- **`repair_browser` tool.** Installs Chromium and starts the crawler on demand, so a degraded server recovers from inside the session. A no-op when healthy, and it waits on an in-flight background repair instead of starting a competing download.
- **Actionable tool errors.** `ping` distinguishes ready, installing, and failed. Crawl tools raise an error naming `repair_browser` and `uv run crawl4ai-setup` plus the underlying cause, instead of an `AttributeError` on a `None` crawler.

## [1.1.2] - 2026-04-21

### Added

- **Playwright preflight check**: The server now verifies the Chromium binary exists before opening stdio transport. If Playwright is missing or the cached Chromium is stale (commonly after `uv sync` upgrades Playwright to a new version), the server exits cleanly with a one-line fix command in the MCP client's log instead of a 60-line anyio TaskGroup traceback.

### Changed

- Troubleshooting section in README expanded with the explicit `uv run crawl4ai-setup` fix for stale-Chromium failures.

## [1.1.1] - 2026-04-03

### Changed

- **Upgraded crawl4ai** from 0.8.0 to 0.8.6. Zero API breaks — fully backwards compatible. Dependency swaps: `tf-playwright-stealth` replaced by `playwright-stealth`, `litellm` replaced by `unclecode-litellm`.

## [1.1.0] - 2026-02-28

### Added

- **Politeness delays**: All batch tools (`crawl_many`, `deep_crawl`, `crawl_sitemap`) now support a `delay` parameter (float, default 0) to add configurable delays between requests. For `crawl_many` and `crawl_sitemap`, this wires a RateLimiter into the dispatcher. For `deep_crawl`, it passes `delay_before_return_html` to crawl4ai. Non-breaking — default is 0 (no delay).
- **Disk persistence**: All batch tools now support an `output_dir` parameter (str, default None) to write per-page `.md` files and a `manifest.json` to disk instead of returning content inline. When set, tools return a metadata summary (file paths) instead of full page content. Non-breaking — default None (existing inline behavior).

Inspired by [sadiuysal/crawl4ai-mcp-server](https://github.com/sadiuysal/crawl4ai-mcp-server) (MIT). Implemented from scratch.

## [1.0.0] - 2026-02-22

### Added

- **Core crawling**: `crawl_url` with full JS rendering, cache control, CSS scoping, custom headers/cookies, and configurable timeouts
- **Batch crawling**: `crawl_many` for concurrent multi-URL crawling with semaphore-based concurrency control
- **Deep crawl**: `deep_crawl` for BFS site crawling with configurable depth and page limits
- **Sitemap crawling**: `crawl_sitemap` for XML sitemap ingestion with gzip and sitemap index support
- **LLM extraction**: `extract_structured` for schema-driven structured JSON extraction via LLM (litellm)
- **CSS extraction**: `extract_css` for deterministic CSS-selector-based structured extraction (no LLM required)
- **Session management**: `create_session`, `list_sessions`, `destroy_session` for persistent browser sessions with cookie/state preservation
- **Profile system**: YAML-based crawl profiles (`default`, `fast`, `js_heavy`, `stealth`) with per-call override merging via `list_profiles`
- **Update management**: `check_update` tool for PyPI version checking with changelog highlights, plus non-blocking startup version check
- **Health check**: `ping` tool for verifying server and browser readiness
- **Error handling**: Structured error responses for all tools — tools return error strings instead of raising exceptions
- **Singleton browser**: `AsyncWebCrawler` created once at server startup, shared across all tool calls
