# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [2.1.0] - 2026-08-16

### Security

- **Per-call credentials no longer escape the call that supplied them.** Two leaks, both demonstrated against a live local server that logged what it actually received.

  **Cookies outlived their call.** Injected cookies are written into a browser context that crawl4ai caches and reuses, and closing the page does not close the context. A crawl that supplied a session cookie therefore authenticated every later crawl of that domain. Observed: one call fetched `/private` with `session_token=SUPER-SECRET`, the next fetched `/public` passing no cookies at all, and the server still received `session_token=SUPER-SECRET`. There was no way to clear it short of restarting the server. Injected cookies are now cleared from every live context when the call ends, including when it raises. Cookies supplied to a named session are deliberately kept, since persisting them across calls is what a session is for.

  **Headers crossed between concurrent calls.** crawl4ai keeps one hook slot per hook type on the crawler strategy, and this server shares a single crawler across all tool calls, so a call's headers were published to every other in-flight call. Observed: a request to one host carried a different host's bearer token, and whichever call finished first cleared the slot out from under the other. This was worst with the batch tools, where one `crawl_url` carrying an auth header overlapping a 20-URL batch would stamp that header on all 20 requests. Per-call header and cookie data now lives in a `contextvars.ContextVar` scoped to the running task, and the hooks are installed once at startup instead of being set and cleared per call.

  Both leaks had the same root cause: per-call data written into state shared by a singleton.

### Fixed

- **`crawl_sitemap`'s `delay` is now an actual politeness delay in `deep_crawl`.** It was wired to `delay_before_return_html`, a per-page "let JavaScript settle" pause that happens *after* the page has already been fetched. So `delay=5` waited 5 seconds per page while still firing requests at crawl4ai's default ~0.1-0.4s cadence: the documented politeness delay was hitting target sites considerably harder than the caller asked for. It now sets `mean_delay`, which is the pacing crawl4ai's internal dispatcher actually applies. `deep_crawl` also gains the `max_concurrent` parameter it never had (it was silently fixed at crawl4ai's default of 5), and now documents that a deep crawl is dispatched by `MemoryAdaptiveDispatcher` — which `crawl_many` and `crawl_sitemap` deliberately avoid — because crawl4ai's deep-crawl strategy accepts no dispatcher.
- **`output_dir` no longer silently destroys pages.** Filenames were derived from the URL by a lossy transform with no uniqueness guarantee, so distinct pages collided and overwrote each other while the crawl still reported full success. Worst case: the character class is ASCII-only, so every path made of non-Latin characters reduced to nothing and left only the domain — four distinct Chinese URLs all wrote to `example_cn.md`, meaning a crawl of any Chinese, Japanese, Korean, Arabic or Cyrillic site kept only its last page. Also collided: `/docs/api` with `/docs/api/`, any two URLs sharing 200 characters, and `/Page` with `/page` on a case-insensitive filesystem (the macOS default). A hash of the full URL is now appended to the readable stem.
- **Profile and per-call settings are validated against crawl4ai itself.** The allowlist was hand-written and listed 20 keys against a `CrawlerRunConfig` that accepts 99, so 77 upstream parameters could not be reached from a profile or a per-call override — `excluded_tags`, `exclude_external_links`, `check_robots_txt`, `remove_consent_popups`, `target_elements`, `only_text` among them. Setting one did nothing, and the warning went to stderr where no MCP client shows it. Valid keys are now read from the live class signature, so this cannot drift again. `list_profiles` also stopped reporting stripped keys as active settings, which meant the tool that exists to describe a profile was describing settings that had no effect.
- **`check_update` distinguishes "no changelog entry" from a plain link.** crawl4ai shipped 0.9.1 and 0.9.2 to PyPI without adding either to CHANGELOG.md, so the lookup silently fell through to a bare URL for the two most recent releases with no sign it had tried.
- **Removed inert browser flags.** `--disable-gpu`, `--disable-dev-shm-usage` and `--no-sandbox` were passed as `extra_args`; crawl4ai already sets all three and dedupes, and the launch arguments are byte-identical with and without them. Worth removing rather than leaving: crawl4ai deliberately drops `--disable-gpu` when stealth is enabled, because disabling the GPU kills WebGL and anti-bot sensors read that as headless, and re-adding it here would have silently undone that.
- **Sitemaps with any XML namespace now parse.** The parser pinned the `sitemaps.org/schemas/sitemap/0.9` namespace URI, so a sitemap declaring any other namespace (the older `google.com/schemas/sitemap/0.84` among them) matched nothing and was reported to the caller as an empty sitemap. Namespaces are now matched with a wildcard. Relative `<loc>` values are resolved against the sitemap's own URL instead of being returned unusable, and invisible characters (zero-width space, BOM) that survive `.strip()` and silently break a URL are removed. Sitemap-index children are fetched concurrently rather than one at a time, a real difference on indexes referencing hundreds of sub-sitemaps, and one failing child no longer discards its siblings. Index recursion is now depth-bounded and cycle-aware, so a sitemap index referencing itself terminates instead of recursing until the process dies.
- **`crawl_sitemap` no longer crashes when the sitemap cannot be read.** Its two early-return error paths still returned strings after the tool was converted to structured output, so Pydantic rejected them at the tool boundary and an unreachable, missing, or non-XML sitemap produced an opaque `1 validation error for CrawlBatchResult` instead of the reason it failed. Found by pointing the tool at a real sitemap index and at an ordinary HTML page. Both now return a `CrawlBatchResult` with `crawled: 0` and an `error`, and a fetch failure is worded differently from an XML parse failure, since "fetch failed" on a page that fetched fine sends you to check the network for nothing.
- **Code examples survive the content filter.** `PruningContentFilter` treated `<pre>` and `<code>` as low-density noise and reassembled them without their whitespace, so a syntax-highlighted docs page (mkdocs-material, Docusaurus and friends wrap every token in its own `<span>`) turned `uvx pycowsay hello from uv` into `uvxpycowsayhellofromuv`. Feeding that to a model is worse than dropping it, because it reads like a real command. The filter now preserves those tags. Measured across three real docs sites (uv, Pydantic, FastAPI): code lines surviving intact went from 1 to 10 of 24, mangled lines from 1 to 0, and retained content nearly doubled (26.9k to 48.6k chars) with nothing getting smaller. crawl4ai's `mark_code` and `handle_code_in_pre` options were measured too and changed nothing, so they are deliberately not set. This is an improvement rather than a cure: `fit_markdown` still drops newlines between statements inside a multi-line block, so callers needing verbatim code should scope with `css_selector` and lower `word_count_threshold`.
- **LLM extraction failures no longer look like data.** crawl4ai does not raise when an LLM call fails; it reports success and puts the failure inside `extracted_content` as blocks flagged `{"error": true}`. `extract_structured` passed that through verbatim, so a retired model name or a bad key came back looking like a normal result with a quiet `Total tokens: 0` underneath. Observed live with a retired Gemini model. The tool now detects the flag (never the word "error", which appears in perfectly good extracted data) and reports the provider's message. Model names in the docstring were also stale and now note that providers retire them.
- **HTTP error pages are detectable.** `crawl4ai` reports `success` for any page it managed to fetch, so a real HTTP 404 arrived with `success: true` and the error page's body as content. `PageResult` carried no status, so a caller filtering on `success` alone silently ingested error pages and had no way to tell. `status_code` is now on every page. `success` is deliberately not flipped on 4xx/5xx, since some callers do want an error page's body; making the status visible is the honest fix.

### Added

- **`status_code` on every page** and **`error` on `CrawlBatchResult`**, both additive to the declared output schema. `status_code` is what makes an HTTP error page distinguishable from real content; `error` reports a whole-operation failure that happened before any page was attempted.

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
