# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
