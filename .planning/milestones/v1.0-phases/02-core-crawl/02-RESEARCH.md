# Phase 2: Core Crawl - Research

**Researched:** 2026-02-19
**Domain:** crawl4ai 0.8.0 AsyncWebCrawler API — CrawlerRunConfig, CrawlResult, content filtering, per-request HTTP overrides
**Confidence:** HIGH (all findings verified against installed 0.8.0 library via live introspection + Context7 official docs)

## Summary

Phase 2 adds the `crawl_url` tool that gives Claude full control over a single-URL crawl. The crawl4ai 0.8.0 API is well-designed and covers almost everything in the requirements natively through `CrawlerRunConfig` — JS code execution, `wait_for`, `css_selector`, `excluded_selector`, `cache_mode`, `user_agent`, `page_timeout`, and the `DefaultMarkdownGenerator` + `PruningContentFilter` pipeline for `fit_markdown` output.

The one non-obvious requirement is per-request custom HTTP headers and cookies (CORE-03). In 0.8.0, `CrawlerRunConfig` does **not** have `headers` or `cookies` parameters (those exist only in `BrowserConfig` which is global). The correct workaround for a single-user MCP server is to inject headers via `crawler.crawler_strategy.set_hook('before_goto', ...)` immediately before `arun()` and clear it immediately after. This has been tested and confirmed working. Cookie injection uses the `on_page_context_created` hook the same way.

A critical transport safety finding: `CrawlerRunConfig` defaults to `verbose=True`, which causes crawl4ai's `AsyncLogger` (backed by Rich's `Console()`) to write to **stdout**. This corrupts the MCP stdio transport. Every `CrawlerRunConfig` created in this server **must** explicitly set `verbose=False`.

**Primary recommendation:** Build `crawl_url` as a single tool in `server.py` now (no submodule split yet). Use `DefaultMarkdownGenerator(content_filter=PruningContentFilter(...))` for default fit_markdown output, `CrawlerRunConfig` for all per-request config, and the `before_goto` hook for per-request custom headers. Always set `verbose=False` on every `CrawlerRunConfig`.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| CORE-01 | User can crawl a single URL and receive clean markdown content (with fit_markdown content filtering applied by default) | `DefaultMarkdownGenerator` + `PruningContentFilter` → `result.markdown.fit_markdown`. See Architecture Patterns §1. |
| CORE-02 | User can control JavaScript rendering per-call (headless/headful toggle, execute arbitrary js_code, wait_for CSS selector before extracting) | `CrawlerRunConfig.js_code` (str or list), `CrawlerRunConfig.wait_for` (css:... or js:...), headless controlled by `BrowserConfig` which is global — see Open Questions §1. |
| CORE-03 | User can pass custom HTTP request parameters per-call (headers dict, cookies dict, user-agent string, timeout seconds) | `CrawlerRunConfig.user_agent` (native, per-request). Headers/cookies: `before_goto` / `on_page_context_created` hook pattern (tested). `page_timeout` in ms per-request. |
| CORE-04 | User can control crawl4ai cache behavior per-call (bypass cache, force refresh, use cached if available) | `CrawlerRunConfig.cache_mode = CacheMode.{ENABLED,BYPASS,DISABLED,READ_ONLY,WRITE_ONLY}`. Default in 0.8.0 is `BYPASS`. |
| CORE-05 | User can specify content extraction scope via CSS selectors (include only matching elements, exclude noise elements) | `CrawlerRunConfig.css_selector` (include scope), `CrawlerRunConfig.excluded_selector` (exclude scope), both native per-request. |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `crawl4ai` | 0.8.0 (pinned) | Web crawling, browser automation, markdown generation | Already installed; all required features verified present |
| `crawl4ai.DefaultMarkdownGenerator` | same | Converts cleaned HTML to markdown | Only officially supported markdown generator |
| `crawl4ai.PruningContentFilter` | same | Scores + prunes low-density nodes for fit_markdown | Recommended for general "meaty text" extraction; no LLM cost |
| `crawl4ai.CacheMode` | same | Enum controlling cache read/write behavior | Only way to control cache in 0.8.0 |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `crawl4ai.BM25ContentFilter` | 0.8.0 | Query-driven content filtering | Phase 4+ (extract tools) — not needed for CORE-01 |
| `mcp.server.fastmcp.Context` | existing | Access `AppContext.crawler` from tool | Same pattern as `ping` tool |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `PruningContentFilter` (default) | No filter (raw_markdown only) | Lower noise floor; requirement says fit_markdown by default |
| `before_goto` hook for headers | New `AsyncWebCrawler` per call | Hook is fast (no browser restart); per-crawler is 2-5s overhead each call |
| Single `crawl_url` tool | Multiple narrow tools | Single tool with optional params is simpler for Claude to use |

**Installation:** No new dependencies — all packages already in `pyproject.toml`.

## Architecture Patterns

### Recommended Project Structure

No new files for Phase 2. Add `crawl_url` to `server.py`. The CLAUDE.md planned module split (`tools/crawl.py`, `config_builder.py`) is for Phase 3+ when complexity warrants it.

```
src/crawl4ai_mcp/
└── server.py    # add crawl_url @mcp.tool() function here
```

### Pattern 1: CrawlerRunConfig + PruningContentFilter (CORE-01)

**What:** Build `CrawlerRunConfig` with `DefaultMarkdownGenerator` wrapping `PruningContentFilter`. Access `result.markdown.fit_markdown` for filtered output, `result.markdown.raw_markdown` for unfiltered.

**When to use:** Every `crawl_url` call — `fit_markdown` is the default output.

**Example:**
```python
# Source: verified against crawl4ai 0.8.0 installed package
from crawl4ai import CrawlerRunConfig, CacheMode, DefaultMarkdownGenerator
from crawl4ai.content_filter_strategy import PruningContentFilter

def _build_run_config(
    cache_mode: CacheMode = CacheMode.BYPASS,
    css_selector: str | None = None,
    excluded_selector: str | None = None,
    word_count_threshold: int = 10,
    wait_for: str | None = None,
    js_code: str | list[str] | None = None,
    user_agent: str | None = None,
    page_timeout: int = 60000,
) -> CrawlerRunConfig:
    md_gen = DefaultMarkdownGenerator(
        content_filter=PruningContentFilter(
            threshold=0.48,
            threshold_type="fixed",
            min_word_threshold=word_count_threshold,
        )
    )
    return CrawlerRunConfig(
        markdown_generator=md_gen,
        cache_mode=cache_mode,
        css_selector=css_selector,
        excluded_selector=excluded_selector,
        wait_for=wait_for,
        js_code=js_code,
        user_agent=user_agent,
        page_timeout=page_timeout,
        verbose=False,   # CRITICAL: prevents stdout corruption
    )
```

### Pattern 2: Per-Request Headers/Cookies via Hooks (CORE-03)

**What:** `CrawlerRunConfig` in 0.8.0 has no `headers` or `cookies` params. Inject per-request via Playwright hooks on the crawler strategy, which is safe for a single-user MCP server (asyncio event loop serializes tool calls).

**When to use:** When caller passes `headers` or `cookies` to `crawl_url`.

**Example:**
```python
# Source: verified via live test against 0.8.0 (httpbin.org/headers confirmed injection)
async def _crawl_with_overrides(
    crawler: AsyncWebCrawler,
    url: str,
    config: CrawlerRunConfig,
    headers: dict | None = None,
    cookies: list | None = None,
) -> CrawlResult:
    """Run arun with per-request header/cookie injection via Playwright hooks."""
    strategy = crawler.crawler_strategy

    if headers:
        async def before_goto(page, context, url, config, **kwargs):
            await page.set_extra_http_headers(headers)
        strategy.set_hook("before_goto", before_goto)

    if cookies:
        async def on_page_context_created(page, context, **kwargs):
            await context.add_cookies(cookies)
        strategy.set_hook("on_page_context_created", on_page_context_created)

    try:
        return await crawler.arun(url=url, config=config)
    finally:
        # Always clear hooks — single-user server, but be tidy
        if headers:
            strategy.set_hook("before_goto", None)
        if cookies:
            strategy.set_hook("on_page_context_created", None)
```

### Pattern 3: CacheMode Mapping (CORE-04)

**What:** Map user-facing cache behavior strings to `CacheMode` enum values.

**CacheMode values in 0.8.0 (verified):**
| User Intent | CacheMode | Behavior |
|-------------|-----------|----------|
| Use cached if available (default) | `CacheMode.ENABLED` | Reads cache, writes on miss |
| Always fetch fresh, don't cache | `CacheMode.BYPASS` | Skips cache entirely (default in CrawlerRunConfig!) |
| Force refresh (re-fetch + overwrite cache) | `CacheMode.WRITE_ONLY` | Fetches fresh, writes to cache, ignores existing |
| Only return cached, fail if missing | `CacheMode.READ_ONLY` | Reads cache only, never fetches |
| Disable all caching | `CacheMode.DISABLED` | Fetch fresh, no read/write |

**Note:** `CrawlerRunConfig` defaults to `CacheMode.BYPASS`, not `ENABLED`. The `arun()` source overrides `None` cache_mode to `CacheMode.ENABLED`. Be explicit.

### Pattern 4: CrawlResult markdown access (CORE-01)

**What:** In 0.8.0, `result.markdown` is a `StringCompatibleMarkdown` wrapping a `MarkdownGenerationResult`. Access sub-fields explicitly.

**DEPRECATED (raises AttributeError in 0.8.0):**
- `result.fit_markdown` — raises `AttributeError`
- `result.fit_html` — raises `AttributeError`
- `result.markdown_v2` — raises `AttributeError`

**CORRECT access pattern:**
```python
# Source: verified against CrawlResult source in 0.8.0
if result.success and result.markdown:
    content = result.markdown.fit_markdown  # filtered (may be None if no filter used)
    raw = result.markdown.raw_markdown       # always present on success
    # Fallback: if fit_markdown is None (no filter configured), use raw_markdown
    output = content or raw
```

### Anti-Patterns to Avoid

- **`CrawlerRunConfig(verbose=True)` (the default!):** AsyncLogger uses Rich `Console()` which writes to stdout. Corrupts MCP transport. Always pass `verbose=False`.
- **`result.fit_markdown` directly:** This is a deprecated property in 0.8.0 that raises `AttributeError`. Use `result.markdown.fit_markdown`.
- **Creating new `AsyncWebCrawler` per tool call:** 2-5 second Chromium startup. Reuse singleton from `AppContext`.
- **`result.markdown_v2`:** Raises `AttributeError` in 0.8.0. Replaced by `result.markdown`.
- **Setting hooks without clearing:** In a server that may handle future concurrent calls (e.g., if MCP protocol supports parallel requests), leaked hooks would apply to the wrong request. Always clear in `finally`.
- **`CrawlerRunConfig(headers=...)` or `(cookies=...)`:** These params do NOT exist in 0.8.0 — `TypeError` is raised. Use hook pattern instead.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Content filtering (nav/noise removal) | Custom HTML parser | `PruningContentFilter` | Handles text density, link density, tag scoring; battle-tested |
| Markdown generation from HTML | html2text or custom | `DefaultMarkdownGenerator` | Produces crawl4ai-aware markdown with proper link handling |
| Wait for dynamic content | `asyncio.sleep()` polling | `CrawlerRunConfig.wait_for` | Playwright-native; supports CSS selectors and JS conditions |
| JS execution | Playwright API directly | `CrawlerRunConfig.js_code` | Managed by crawl4ai; integrated with the crawl lifecycle |
| Cache layer | Custom SQLite/disk cache | `CacheMode` + built-in DB | crawl4ai manages its own SQLite cache at `~/.crawl4ai/` |

**Key insight:** The crawl4ai 0.8.0 API covers all Phase 2 requirements except per-request headers/cookies. Don't build workarounds for anything else.

## Common Pitfalls

### Pitfall 1: verbose=True Corrupts MCP Transport (CRITICAL)

**What goes wrong:** Tool call triggers verbose crawl4ai logging → AsyncLogger.log() → Rich Console.print() → writes formatted text to **stdout** → JSON-RPC parser sees malformed frame → MCP disconnects.

**Why it happens:** `CrawlerRunConfig` defaults `verbose=True`. Easy to miss when constructing config.

**How to avoid:** Always construct `CrawlerRunConfig(verbose=False, ...)`. Add a ruff lint rule or code review check.

**Warning signs:** MCP client disconnects or reports parse error during/after first crawl_url call.

### Pitfall 2: Accessing Deprecated Properties on CrawlResult

**What goes wrong:** `result.fit_markdown`, `result.markdown_v2`, or `result.fit_html` → `AttributeError: The 'fit_markdown' attribute is deprecated...`

**Why it happens:** crawl4ai 0.8.0 migrated markdown to a MarkdownGenerationResult object. Old string properties still exist but raise on access.

**How to avoid:** Always use `result.markdown.fit_markdown` and `result.markdown.raw_markdown`. Test with actual crawl before shipping.

**Warning signs:** `AttributeError` with message mentioning deprecated attributes.

### Pitfall 3: Hook Leakage (Low Risk for Single-User, but Real)

**What goes wrong:** If a hook is set before `arun()` and an exception occurs before the `finally` block clears it, the hook persists and affects the next call.

**Why it happens:** Missing try/finally around hook-based calls.

**How to avoid:** Always use `try/finally` to clear hooks (Pattern 2 shows this).

**Warning signs:** Headers/cookies from a previous call appearing in unrelated requests.

### Pitfall 4: CacheMode.BYPASS is the CrawlerRunConfig Default (Confusing)

**What goes wrong:** Default `CrawlerRunConfig()` uses `CacheMode.BYPASS` (never reads cache), not `CacheMode.ENABLED`. Developers expect default to "use cache if available" but fresh fetch happens every time.

**Why it happens:** Design choice in crawl4ai to be explicit-first.

**How to avoid:** Always explicitly pass `cache_mode`. Use `CacheMode.ENABLED` as the tool's default for normal use (shows cached results), and let the user override to `BYPASS` for fresh fetches.

**Warning signs:** Slow responses on repeated crawls when expecting cache hits.

### Pitfall 5: fit_markdown is None When No Content Filter Is Set

**What goes wrong:** Code assumes `result.markdown.fit_markdown` is always a string, but it's `Optional[str]` and is `None` when no `content_filter` is passed to `DefaultMarkdownGenerator`.

**Why it happens:** `fit_markdown` is only populated when a content filter actually runs.

**How to avoid:** Always pass `PruningContentFilter` to `DefaultMarkdownGenerator` in `crawl_url`. Add a fallback: `content = result.markdown.fit_markdown or result.markdown.raw_markdown`.

## Code Examples

Verified patterns from live 0.8.0 introspection and Context7 official docs:

### Minimal Working crawl_url Skeleton

```python
# Source: synthesized from live 0.8.0 introspection + Context7 crawl4ai docs
from crawl4ai import CrawlerRunConfig, CacheMode, DefaultMarkdownGenerator
from crawl4ai.content_filter_strategy import PruningContentFilter
from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession

@mcp.tool()
async def crawl_url(
    url: str,
    cache_mode: str = "enabled",         # enabled|bypass|disabled|read_only|write_only
    css_selector: str | None = None,      # scope include
    excluded_selector: str | None = None, # scope exclude
    wait_for: str | None = None,          # "css:#element" or "js:() => cond"
    js_code: str | None = None,           # JS to run after page load
    user_agent: str | None = None,        # per-call UA override
    headers: dict | None = None,          # per-call HTTP headers (hook-injected)
    cookies: list | None = None,          # per-call cookies [{name,value,domain}]
    page_timeout: int = 60,               # seconds (converted to ms internally)
    ctx: Context[ServerSession, AppContext] = None,
) -> str:
    app: AppContext = ctx.request_context.lifespan_context

    cache_map = {
        "enabled": CacheMode.ENABLED,
        "bypass": CacheMode.BYPASS,
        "disabled": CacheMode.DISABLED,
        "read_only": CacheMode.READ_ONLY,
        "write_only": CacheMode.WRITE_ONLY,
    }
    resolved_cache = cache_map.get(cache_mode, CacheMode.ENABLED)

    md_gen = DefaultMarkdownGenerator(
        content_filter=PruningContentFilter(threshold=0.48, threshold_type="fixed", min_word_threshold=10)
    )
    run_cfg = CrawlerRunConfig(
        markdown_generator=md_gen,
        cache_mode=resolved_cache,
        css_selector=css_selector,
        excluded_selector=excluded_selector,
        wait_for=wait_for,
        js_code=js_code,
        user_agent=user_agent,
        page_timeout=page_timeout * 1000,   # CrawlerRunConfig expects milliseconds
        verbose=False,                       # CRITICAL: prevent stdout corruption
    )

    result = await _crawl_with_overrides(app.crawler, url, run_cfg, headers, cookies)

    if not result.success:
        return _format_crawl_error(url, result)

    md = result.markdown
    content = (md.fit_markdown or md.raw_markdown) if md else ""
    return content
```

### wait_for Syntax Reference

```python
# CSS selector wait (wait until element exists in DOM)
wait_for="css:#main-content"
wait_for="css:.data-loaded"

# JavaScript condition wait (wait until JS expression returns truthy)
wait_for="js:() => document.readyState === 'complete'"
wait_for="js:() => window.dataLoaded === true"
```

### js_code Syntax Reference

```python
# Single JS expression
js_code = "window.scrollTo(0, document.body.scrollHeight);"

# List for sequential execution
js_code = [
    "document.querySelector('button.load-more')?.click();",
    "await new Promise(r => setTimeout(r, 500));",
]
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `result.fit_markdown` (string property) | `result.markdown.fit_markdown` (nested property) | 0.8.0 | Direct property access raises AttributeError |
| `result.markdown_v2` | `result.markdown` (returns StringCompatibleMarkdown) | 0.8.0 | Access `.raw_markdown`, `.fit_markdown` as sub-properties |
| `CrawlerRunConfig(headers=...)` | `before_goto` hook pattern | As of 0.8.0 | Per-request headers require workaround in 0.8.0 |
| `async with AsyncWebCrawler()` | explicit `start()`/`close()` in lifespan | Phase 1 decision | Required because lifespan IS the context manager |

**Deprecated/outdated:**
- `result.fit_markdown`: Raises `AttributeError` in 0.8.0. Use `result.markdown.fit_markdown`.
- `result.markdown_v2`: Raises `AttributeError`. Use `result.markdown`.
- `result.fit_html`: Raises `AttributeError`. Use `result.markdown.fit_html`.
- `CrawlerRunConfig` kwargs-based construction (`CrawlerRunConfig.from_kwargs()`): Supported for backward compat but deprecated; use direct constructor.

## Open Questions

1. **CORE-02: headless/headful toggle per-call**
   - What we know: `BrowserConfig.headless` is set at server startup (singleton). `CrawlerRunConfig` has no headless field.
   - What's unclear: Whether the requirement literally means toggling headless per-call, or simply means "JS rendering is on by default" (which it is since the browser is Chromium).
   - Recommendation: Interpret as "JS rendering is enabled by default and js_code/wait_for control it." Do not expose a headless toggle on the tool — toggling headless requires recreating the browser (singleton violation). If truly needed later, document it as a server restart config, not a per-call option. For Phase 2, `js_code` + `wait_for` cover the intent of "JS rendering control".

2. **Cookie format for `context.add_cookies()`**
   - What we know: Playwright `context.add_cookies()` accepts a list of dicts with `name`, `value`, `domain`/`url`, and optionally `path`, `expires`, `httpOnly`, `secure`, `sameSite`.
   - What's unclear: Whether to validate cookie shape in the tool or pass through raw.
   - Recommendation: Accept `cookies` as `list[dict]` and pass through directly. Document required fields in the tool docstring. Let Playwright raise if malformed.

3. **page_timeout units: seconds vs. milliseconds in the tool interface**
   - What we know: `CrawlerRunConfig.page_timeout` is in **milliseconds** (default 60000). It's user-facing as "seconds" in the requirement.
   - What's unclear: Which unit is most natural for the Claude tool interface.
   - Recommendation: Accept seconds in the tool interface (natural for humans), multiply by 1000 internally. Document clearly in the docstring.

## Sources

### Primary (HIGH confidence)
- Live introspection of `/Users/brianpotter/ai_tools/crawl4ai_mcp/.venv/lib/python3.12/site-packages/crawl4ai/` — full source read of `CrawlerRunConfig`, `CrawlResult`, `AsyncWebCrawler.arun`, `AsyncPlaywrightCrawlerStrategy`, `BrowserManager`, `AsyncLogger`
- Live functional tests: `PruningContentFilter` pipeline, hook-based header injection (verified via httpbin.org/headers), fit_markdown access pattern (verified via example.com crawl)
- `/websites/crawl4ai` Context7 library — official docs snippets for `CrawlerRunConfig`, `DefaultMarkdownGenerator`, `PruningContentFilter`, `CacheMode`, `js_code`, `wait_for`, `css_selector`
- Phase 1 codebase: `src/crawl4ai_mcp/server.py` — established patterns for `AppContext`, lifespan, `_format_crawl_error`, tool structure

### Secondary (MEDIUM confidence)
- Context7 advanced crawl4ai docs showing `CrawlerRunConfig(headers=...)` — verified this does NOT work in installed 0.8.0 (raises TypeError); the docs appear to describe a later version

### Tertiary (LOW confidence)
- None

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — verified via live introspection of installed 0.8.0
- Architecture: HIGH — patterns tested with live crawls
- Pitfalls: HIGH — all critical pitfalls reproduced in live tests (verbose stdout, deprecated property AttributeError)
- Per-request headers/cookies workaround: HIGH — tested and confirmed working

**Research date:** 2026-02-19
**Valid until:** 2026-03-21 (crawl4ai is pinned to 0.8.0; findings stable until pin changes)
