# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
