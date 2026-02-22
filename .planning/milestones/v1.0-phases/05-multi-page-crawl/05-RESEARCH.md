# Phase 5: Multi-Page Crawl - Research

**Researched:** 2026-02-21
**Domain:** crawl4ai 0.8.0 multi-URL and deep crawling APIs -- `arun_many`, `BFSDeepCrawlStrategy`, dispatchers, filter chains, sitemap parsing
**Confidence:** HIGH (all findings verified against installed 0.8.0 source code + Context7 official docs)

## Summary

Phase 5 adds three tools for crawling beyond single pages: `crawl_many` (parallel batch), `deep_crawl` (BFS link-following), and `crawl_sitemap` (XML sitemap parsing + batch crawl). The crawl4ai 0.8.0 API provides strong native support for the first two via `arun_many()` and `BFSDeepCrawlStrategy`, but has no built-in sitemap XML parser -- that must be implemented manually using `xml.etree.ElementTree` (stdlib) + `httpx` (already a transitive dependency) to fetch and parse sitemap XML, then feed discovered URLs to `arun_many()`.

The `arun_many()` method accepts a `dispatcher` parameter for concurrency control. Two dispatchers are available: `SemaphoreDispatcher` (fixed concurrency cap) and `MemoryAdaptiveDispatcher` (dynamic, memory-aware). The default dispatcher is `MemoryAdaptiveDispatcher` with a rate limiter. For an MCP server, `SemaphoreDispatcher` is the better default -- it provides predictable behavior without the complexity of memory monitoring, which can be overly conservative on a dev machine. Neither dispatcher should be created with a `monitor` parameter -- `CrawlerMonitor` uses Rich's `Console()` which writes to stdout and would corrupt the MCP transport.

Deep crawling via `BFSDeepCrawlStrategy` is configured through `CrawlerRunConfig.deep_crawl_strategy` and invoked via the normal `crawler.arun()` method (a `DeepCrawlDecorator` intercepts the call). The strategy handles BFS traversal, link discovery, deduplication, `max_depth`, `max_pages`, `include_external`, and `FilterChain` for URL pattern filtering. This is exactly what MULTI-02 requires. The strategy internally calls `arun_many()` for each BFS level, so it benefits from the same dispatcher infrastructure.

The user's decisions emphasize maximum agent flexibility: high default `max_pages` (100+), no runtime timeout, no artificial concurrency cap, and agent-configurable domain scope and URL pattern filtering. The tool descriptions should explicitly communicate all available controls.

**Primary recommendation:** Use `SemaphoreDispatcher` (no monitor) for `crawl_many`, `BFSDeepCrawlStrategy` with `FilterChain` for `deep_crawl`, and manual sitemap parsing with `httpx` + `xml.etree.ElementTree` feeding into `arun_many()` for `crawl_sitemap`. Always pass `verbose=False` on all `CrawlerRunConfig` objects. Never pass `monitor` to any dispatcher.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

#### Design philosophy
- The MCP server should expose **maximum flexibility through parameters** and let the AI agent decide what it wants
- Tool descriptions should make clear that controls exist and are adjustable (page count, detail level, concurrency, scope, etc.)
- The agent -- not the server -- decides how many pages to return, level of detail, output structure, etc.

#### Result format
- Claude's Discretion: output structure, whether to return per-URL blocks vs summary + content, markdown vs JSON, level of detail
- The tools should expose parameters that give the agent control over what comes back

#### Safety limits
- Default `max_pages` for `deep_crawl`: **100+** (higher than the originally proposed 50)
- No runtime timeout for multi-page operations -- page count limits are sufficient
- No artificial concurrency cap on `crawl_many` -- the agent decides parallelism
- All limits should be agent-configurable per call

#### Partial failure handling
- **Always return both** successes and failures -- never discard successful results because of individual URL errors
- Never fail an entire batch for individual errors
- Claude's Discretion: error detail level, BFS traversal resilience strategy, progress reporting approach

#### Deep crawl scope
- Link-following domain scope is **agent-configurable** -- expose a parameter for same-domain, same-root-domain, or any domain
- **URL pattern filtering**: include an optional `include_pattern`/`exclude_pattern` param so the agent can filter which links to follow (e.g., "only follow /docs/* links")
- **Always deduplicate** -- each unique URL is crawled at most once, regardless of how many pages link to it
- No robots.txt enforcement -- compliance is the user's responsibility (consistent with project out-of-scope list)

### Claude's Discretion
- Output structure and format for all three tools
- Sensible hard cap ceiling (if any)
- BFS branch resilience when dead zones are hit
- Progress reporting strategy (intermediate updates vs final results)
- Error detail level in failure reports
- Compression/summarization of large result sets

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| MULTI-01 | User can crawl multiple URLs in parallel (pass list of URLs; returns all results concurrently via arun_many()) | `arun_many()` is native to AsyncWebCrawler 0.8.0. Accepts `urls: List[str]`, `config: CrawlerRunConfig`, `dispatcher: BaseDispatcher`. Returns `List[CrawlResult]` in batch mode. See Architecture Patterns 1. |
| MULTI-02 | User can deep-crawl a site via link-following with BFS strategy (start URL + max_depth + max_pages hard limit; max_pages defaults to 50) | `BFSDeepCrawlStrategy` in `crawl4ai.deep_crawling` with `max_depth`, `max_pages`, `include_external`, `filter_chain`. Invoked via `CrawlerRunConfig(deep_crawl_strategy=...)` + `crawler.arun()`. User decision: default max_pages should be 100+ (overrides REQUIREMENTS.md's 50). See Architecture Patterns 2. |
| MULTI-03 | User can crawl all URLs from a sitemap (pass sitemap XML URL; server fetches, parses, and crawls all discovered URLs) | No built-in sitemap parser in crawl4ai 0.8.0. Implement manually: fetch XML via `httpx`, parse with `xml.etree.ElementTree`, extract `<loc>` URLs, feed to `arun_many()`. See Architecture Patterns 3. |
| MULTI-04 | Deep crawl and batch crawl enforce hard limits on page count and total runtime to prevent runaway crawls | `BFSDeepCrawlStrategy.max_pages` enforces page count natively (stops link discovery and crawling when reached). User decision: no runtime timeout -- page count limits are sufficient. For `crawl_many` / `crawl_sitemap`, the URL list length IS the limit. See Common Pitfalls 3. |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `crawl4ai.AsyncWebCrawler.arun_many()` | 0.8.0 | Batch-crawl multiple URLs with concurrency control | Native method on installed AsyncWebCrawler; handles dispatching, rate limiting, result aggregation |
| `crawl4ai.deep_crawling.BFSDeepCrawlStrategy` | 0.8.0 | BFS deep crawl with max_depth, max_pages, filter_chain | Native strategy; integrates with `arun()` via `DeepCrawlDecorator`; handles deduplication, link discovery, depth tracking |
| `crawl4ai.async_dispatcher.SemaphoreDispatcher` | 0.8.0 | Fixed concurrency cap for `arun_many` | Predictable behavior; does not require memory monitoring; appropriate for single-user MCP server |
| `crawl4ai.deep_crawling.FilterChain` | 0.8.0 | Chain of URL filters for deep crawl scope control | Composes DomainFilter + URLPatternFilter for flexible agent-configurable filtering |
| `crawl4ai.deep_crawling.DomainFilter` | 0.8.0 | Restrict deep crawl to specific domains | Supports `allowed_domains` and `blocked_domains` with subdomain matching |
| `crawl4ai.deep_crawling.URLPatternFilter` | 0.8.0 | Include/exclude URLs by glob or regex pattern | Supports glob (e.g., `/docs/*`), regex, and `reverse=True` for exclusion |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `httpx` | 0.28.1 (transitive dep) | Fetch sitemap XML without browser overhead | `crawl_sitemap` tool -- sitemaps are plain XML, no browser needed |
| `xml.etree.ElementTree` | stdlib | Parse sitemap XML, extract `<loc>` URLs | `crawl_sitemap` tool -- lightweight, no extra dependency |
| `crawl4ai.async_dispatcher.RateLimiter` | 0.8.0 | Per-domain rate limiting with exponential backoff | Optional on dispatchers; useful for polite crawling of single domains |
| `crawl4ai.async_dispatcher.MemoryAdaptiveDispatcher` | 0.8.0 | Memory-aware concurrency control | Alternative to SemaphoreDispatcher when memory pressure is a concern |
| `crawl4ai.CrawlerRunConfig.clone()` | 0.8.0 | Create modified config copies | Used internally by BFSDeepCrawlStrategy to disable deep_crawl_strategy recursion per level |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `SemaphoreDispatcher` (default) | `MemoryAdaptiveDispatcher` | More adaptive but uses `psutil` memory monitoring, can be overly conservative on dev machines with other apps running |
| `httpx` for sitemap fetch | `aiohttp` (also available) | Both work; httpx is simpler API and already a crawl4ai dep |
| `httpx` for sitemap fetch | Browser-based crawl via `arun()` | Massive overkill -- sitemaps are plain XML; browser adds 2-5s overhead per fetch |
| `xml.etree.ElementTree` | `lxml` | lxml is faster but requires C extension; ET is stdlib and sitemaps are small |
| Manual sitemap parser | `crawl4ai.aseed_urls()` | `aseed_urls` discovers URLs from domain (including sitemap), but has its own logger and is designed for URL seeding, not direct sitemap URL input |

**Installation:** No new dependencies needed. `httpx` and `aiohttp` are already transitive dependencies of crawl4ai. `xml.etree.ElementTree` is stdlib.

## Architecture Patterns

### Recommended Project Structure

```
src/crawl4ai_mcp/
├── server.py           # Existing: add crawl_many, deep_crawl, crawl_sitemap tools
├── profiles.py         # Existing: build_run_config reused for multi-page config
└── tools/              # NOT YET — keep tools in server.py for now (consistent with phases 1-4)
```

The CLAUDE.md planned `tools/batch.py` split is deferred until the file grows unwieldy. All three new tools go in `server.py` for consistency with existing tools.

### Pattern 1: crawl_many via arun_many + SemaphoreDispatcher (MULTI-01)

**What:** Wrap `arun_many()` with a `SemaphoreDispatcher` (no monitor). Build `CrawlerRunConfig` via the existing `build_run_config()` from profiles.py. Aggregate results into a structured string with per-URL blocks.

**When to use:** When the agent has a list of URLs to crawl in parallel.

**Critical details:**
- `arun_many()` returns `List[CrawlResult]` in batch mode (default). Each result has `result.success`, `result.url`, `result.markdown`, `result.error_message`.
- Default dispatcher is `MemoryAdaptiveDispatcher` with a RateLimiter. Override with `SemaphoreDispatcher` for predictable MCP behavior.
- NEVER pass `monitor=CrawlerMonitor(...)` -- it uses Rich Console which writes to stdout.
- Each CrawlResult also gets a `dispatch_result` attribute with timing/memory metadata.
- `arun_many()` never raises for individual URL failures -- failed URLs appear as `CrawlResult(success=False)` in the results list.

**Example:**
```python
# Source: verified against installed crawl4ai 0.8.0 async_webcrawler.py
from crawl4ai.async_dispatcher import SemaphoreDispatcher

@mcp.tool()
async def crawl_many(
    urls: list[str],
    max_concurrent: int = 10,
    profile: str | None = None,
    # ... other crawl params matching crawl_url ...
    ctx: Context[ServerSession, AppContext] = None,
) -> str:
    app: AppContext = ctx.request_context.lifespan_context
    run_cfg = build_run_config(app.profile_manager, profile, **per_call_kwargs)

    dispatcher = SemaphoreDispatcher(
        semaphore_count=max_concurrent,
        # NO monitor -- CrawlerMonitor uses Rich Console -> stdout corruption
    )

    results = await app.crawler.arun_many(
        urls=urls,
        config=run_cfg,
        dispatcher=dispatcher,
    )

    # Aggregate results
    successes = [r for r in results if r.success]
    failures = [r for r in results if not r.success]
    # ... format output ...
```

### Pattern 2: deep_crawl via BFSDeepCrawlStrategy (MULTI-02)

**What:** Create a `BFSDeepCrawlStrategy` with agent-configurable `max_depth`, `max_pages`, `include_external`, and `FilterChain` (for domain and URL pattern filtering). Pass it via `CrawlerRunConfig(deep_crawl_strategy=...)` and call `crawler.arun()`.

**When to use:** When the agent wants to discover and crawl linked pages starting from a seed URL.

**Critical details:**
- `BFSDeepCrawlStrategy` is instantiated per call (it has mutable state: `_pages_crawled`, `_cancel_event`, `stats`).
- The strategy uses `arun_many()` internally for each BFS level (batches URLs per depth).
- `include_external=False` stays within the start URL's domain (but includes subdomains via link classification). `include_external=True` follows all links.
- `FilterChain` composes `DomainFilter` and `URLPatternFilter` for fine-grained control.
- `max_pages` counts only successful crawls (failed URLs don't count toward the limit).
- Results include `result.metadata["depth"]` and `result.metadata["parent_url"]`.
- `crawler.arun()` returns `List[CrawlResult]` when `deep_crawl_strategy` is set (not a single result).
- The internal `arun_many()` calls will use the default `MemoryAdaptiveDispatcher` unless we override -- we should consider injecting `SemaphoreDispatcher` via config cloning.

**Domain scope mapping:**
| Agent Parameter | FilterChain Configuration |
|-----------------|--------------------------|
| `"same-domain"` | `include_external=False` (default) -- stays within start URL's domain |
| `"same-origin"` | `include_external=False` + `DomainFilter(allowed_domains=[exact_start_domain])` |
| `"any"` | `include_external=True` -- follows all links |
| `include_pattern` | `URLPatternFilter(patterns=[pattern], use_glob=True)` in FilterChain |
| `exclude_pattern` | `URLPatternFilter(patterns=[pattern], use_glob=True, reverse=True)` in FilterChain |

**Example:**
```python
# Source: verified against installed crawl4ai 0.8.0 deep_crawling/bfs_strategy.py
from crawl4ai.deep_crawling import BFSDeepCrawlStrategy, FilterChain, DomainFilter, URLPatternFilter

@mcp.tool()
async def deep_crawl(
    url: str,
    max_depth: int = 2,
    max_pages: int = 100,
    include_external: bool = False,
    include_pattern: str | None = None,
    exclude_pattern: str | None = None,
    # ... other crawl params ...
    ctx: Context[ServerSession, AppContext] = None,
) -> str:
    # Build filter chain from agent params
    filters = []
    if include_pattern:
        filters.append(URLPatternFilter(patterns=[include_pattern]))
    if exclude_pattern:
        filters.append(URLPatternFilter(patterns=[exclude_pattern], reverse=True))
    filter_chain = FilterChain(filters) if filters else FilterChain()

    strategy = BFSDeepCrawlStrategy(
        max_depth=max_depth,
        max_pages=max_pages,
        include_external=include_external,
        filter_chain=filter_chain,
    )

    app: AppContext = ctx.request_context.lifespan_context
    run_cfg = build_run_config(
        app.profile_manager, profile,
        deep_crawl_strategy=strategy,
        **per_call_kwargs,
    )

    results = await app.crawler.arun(url=url, config=run_cfg)
    # results is List[CrawlResult] when deep_crawl_strategy is set
    # ... format output with depth info ...
```

### Pattern 3: crawl_sitemap via httpx + ET + arun_many (MULTI-03)

**What:** Fetch sitemap XML via `httpx.AsyncClient`, parse with `xml.etree.ElementTree` to extract `<loc>` URLs, then crawl them via `arun_many()`.

**When to use:** When the agent has a sitemap URL and wants to crawl all (or some) listed pages.

**Critical details:**
- Sitemaps use the namespace `http://www.sitemaps.org/schemas/sitemap/0.9` -- must handle in ET parsing.
- Sitemap index files (`<sitemapindex>`) contain `<sitemap><loc>` entries pointing to sub-sitemaps -- must recursively fetch.
- Sitemaps can be gzip-compressed (`.xml.gz`) -- `httpx` does not auto-decompress; use `gzip.decompress()`.
- Use `httpx.AsyncClient` (not the browser) because sitemaps are plain XML -- no JS rendering needed.
- Feed discovered URLs to `arun_many()` with a `SemaphoreDispatcher`.

**Example:**
```python
import gzip
import httpx
import xml.etree.ElementTree as ET

SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

async def _fetch_sitemap_urls(sitemap_url: str) -> list[str]:
    """Fetch and parse a sitemap XML, returning all <loc> URLs."""
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        resp = await client.get(sitemap_url)
        resp.raise_for_status()

    content = resp.content
    if sitemap_url.endswith(".gz"):
        content = gzip.decompress(content)

    root = ET.fromstring(content)

    # Check if this is a sitemap index
    sub_sitemaps = root.findall("sm:sitemap/sm:loc", SITEMAP_NS)
    if sub_sitemaps:
        urls = []
        for loc in sub_sitemaps:
            urls.extend(await _fetch_sitemap_urls(loc.text.strip()))
        return urls

    # Regular sitemap -- extract <url><loc> entries
    return [loc.text.strip() for loc in root.findall("sm:url/sm:loc", SITEMAP_NS)]
```

### Anti-Patterns to Avoid

- **Passing `monitor=CrawlerMonitor(...)` to any dispatcher:** `CrawlerMonitor` uses Rich's `Console()` which writes to stdout. This WILL corrupt the MCP stdio transport. Always pass `monitor=None` (the default when omitted).
- **Creating `BFSDeepCrawlStrategy` as a singleton:** The strategy has mutable state (`_pages_crawled`, `_cancel_event`). Must be instantiated fresh per `deep_crawl` tool call.
- **Using `MemoryAdaptiveDispatcher` without understanding its defaults:** Default `memory_threshold_percent=90.0` can be overly conservative on a dev machine with browser tabs and IDE open. `SemaphoreDispatcher` is more predictable for an MCP server.
- **Setting `verbose=True` on `CrawlerRunConfig` passed to deep crawl:** BFS internally calls `arun_many()` which creates cloned configs -- but the original config's verbose leaks through the `AsyncLogger`. Always `verbose=False`.
- **Assuming `arun_many()` order matches input URL order:** Results are returned as they complete, not in input order. Match by `result.url` if order matters.
- **Ignoring sitemap namespace:** Sitemap XML uses `xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"`. Queries without namespace prefix return empty results.
- **Crawling sitemap XML with the browser:** Sitemaps are plain XML. Using `arun()` to fetch them wastes a Chromium tab, is slower, and the HTML parser may mangle the XML. Use `httpx` instead.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Parallel URL crawling | Custom asyncio.gather() loop with crawler.arun() | `crawler.arun_many()` with dispatcher | arun_many handles dispatching, rate limiting, result aggregation, and memory tracking |
| BFS link-following | Manual queue + visited set + arun() loop | `BFSDeepCrawlStrategy` | Handles deduplication, depth tracking, max_pages enforcement, link scoring, filter chains |
| URL deduplication in deep crawl | Custom visited set | `BFSDeepCrawlStrategy` internal `visited` set with URL normalization | Strategy normalizes URLs (strips fragments, handles trailing slashes) before dedup |
| Domain filtering | Custom urlparse() domain check | `DomainFilter(allowed_domains=[...])` | Handles subdomains, case normalization, has LRU cache for performance |
| URL pattern matching | Custom regex | `URLPatternFilter(patterns=[...])` | Handles glob, regex, prefix, suffix, domain patterns with LRU cache |
| Concurrency limiting | Custom asyncio.Semaphore wrapper | `SemaphoreDispatcher(semaphore_count=N)` | Integrates with crawl4ai's task tracking and error handling |

**Key insight:** crawl4ai 0.8.0 provides almost everything Phase 5 needs natively. The only custom code is (1) sitemap XML parsing, (2) tool parameter wiring, and (3) result formatting. Do not reimplement crawling, link discovery, dedup, or concurrency control.

## Common Pitfalls

### Pitfall 1: CrawlerMonitor Corrupts stdout (CRITICAL)

**What goes wrong:** Passing `monitor=CrawlerMonitor(...)` to a dispatcher causes Rich's `Console()` to write formatted progress tables to stdout, corrupting the MCP stdio transport.

**Why it happens:** `CrawlerMonitor` imports `rich.console.Console` and `rich.live.Live` for terminal UI rendering. These write to stdout by default.

**How to avoid:** Never pass `monitor` to any dispatcher in the MCP server. Omit the parameter entirely (defaults to `None`).

**Warning signs:** MCP client disconnects during multi-URL crawls. Works for single-URL `crawl_url` but fails on `crawl_many`.

### Pitfall 2: BFSDeepCrawlStrategy Mutable State

**What goes wrong:** Reusing a `BFSDeepCrawlStrategy` instance across tool calls causes `_pages_crawled` to accumulate, `_cancel_event` to stay set, and `visited` URLs from previous crawls to persist.

**Why it happens:** The strategy tracks state in instance variables that are not reset between calls.

**How to avoid:** Create a new `BFSDeepCrawlStrategy` instance for every `deep_crawl` tool call. Never store it in `AppContext`.

**Warning signs:** Second deep crawl returns fewer pages than expected, or starts with non-zero `_pages_crawled`.

### Pitfall 3: No Runtime Timeout (By Design, But Be Aware)

**What goes wrong:** A deep crawl with `max_pages=1000` and `max_depth=5` on a large site can take many minutes. There is no timeout to cut it short.

**Why it happens:** User decision: no runtime timeout for multi-page operations -- page count limits are sufficient.

**How to avoid:** Set sensible `max_pages` defaults (100 per user decision). Document in tool description that large values take proportionally longer. The agent controls the timeout by choosing `max_pages` and `max_depth`.

**Warning signs:** Tool call appears to hang for minutes. This is expected behavior for large crawls, not a bug.

### Pitfall 4: Default Dispatcher Rate Limiter Slows Batch Crawls

**What goes wrong:** `MemoryAdaptiveDispatcher` (the default when no dispatcher is passed) includes a `RateLimiter(base_delay=(1.0, 3.0))`, adding 1-3 seconds of delay between requests to the same domain. For 100 URLs on the same domain, this adds 100-300 seconds of artificial delay.

**Why it happens:** Rate limiting is polite but slow. The default is designed for general web crawling, not fast batch extraction.

**How to avoid:** Use `SemaphoreDispatcher` without a `RateLimiter` as the default for `crawl_many`. Optionally expose a `rate_limit` parameter so the agent can enable it when needed.

**Warning signs:** Batch crawls are 10x slower than expected. Each URL takes ~2s even for fast sites.

### Pitfall 5: Sitemap Namespace Handling

**What goes wrong:** `ET.findall("url/loc", root)` returns empty list because sitemap XML uses the namespace `http://www.sitemaps.org/schemas/sitemap/0.9`.

**Why it happens:** XML namespaces require explicit namespace prefix in XPath queries with ElementTree.

**How to avoid:** Always use namespace dict: `root.findall("sm:url/sm:loc", {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"})`.

**Warning signs:** `crawl_sitemap` returns "No URLs found in sitemap" despite the sitemap being valid.

### Pitfall 6: deep_crawl_strategy in build_run_config

**What goes wrong:** `build_run_config()` in `profiles.py` strips unknown keys. `deep_crawl_strategy` is a valid `CrawlerRunConfig` kwarg but may not be in `KNOWN_KEYS` or `_PER_CALL_KEYS`.

**Why it happens:** Phase 3 defined `KNOWN_KEYS` as a finite set of known CrawlerRunConfig params. `deep_crawl_strategy` was not needed until now.

**How to avoid:** Add `"deep_crawl_strategy"` to `_PER_CALL_KEYS` in `profiles.py`, OR build the CrawlerRunConfig for deep_crawl directly (bypassing build_run_config), similar to how extraction tools handle config in Phase 4.

**Warning signs:** `logger.warning("Stripping unknown profile keys ['deep_crawl_strategy']")` and deep crawl returns only a single page.

### Pitfall 7: arun() Return Type Changes with deep_crawl_strategy

**What goes wrong:** Code assumes `crawler.arun()` returns a single `CrawlResult`, but when `deep_crawl_strategy` is set, it returns `List[CrawlResult]`.

**Why it happens:** `DeepCrawlDecorator` intercepts `arun()` and delegates to the strategy's `_arun_batch()`, which returns a list.

**How to avoid:** Type the return value correctly. When `deep_crawl_strategy` is configured, always expect `List[CrawlResult]`.

**Warning signs:** `TypeError: 'list' object has no attribute 'success'` when trying to access single-result properties.

## Code Examples

Verified patterns from installed crawl4ai 0.8.0 source code:

### arun_many with SemaphoreDispatcher

```python
# Source: verified against installed 0.8.0 async_webcrawler.py + async_dispatcher.py
from crawl4ai.async_dispatcher import SemaphoreDispatcher

# Create dispatcher with fixed concurrency, NO monitor
dispatcher = SemaphoreDispatcher(
    semaphore_count=10,
    # NO rate_limiter for fast batch crawl (add if needed for polite crawling)
    # NO monitor — CrawlerMonitor uses Rich Console -> stdout corruption
)

# arun_many returns List[CrawlResult] in batch mode
results: list[CrawlResult] = await crawler.arun_many(
    urls=["https://example.com/page1", "https://example.com/page2"],
    config=run_cfg,  # CrawlerRunConfig with verbose=False
    dispatcher=dispatcher,
)

# Results include both successes and failures
for result in results:
    if result.success:
        content = result.markdown.fit_markdown or result.markdown.raw_markdown
    else:
        error = result.error_message
```

### BFSDeepCrawlStrategy with FilterChain

```python
# Source: verified against installed 0.8.0 deep_crawling/bfs_strategy.py + filters.py
from crawl4ai.deep_crawling import (
    BFSDeepCrawlStrategy,
    FilterChain,
    DomainFilter,
    URLPatternFilter,
)

# Build filter chain for agent-specified constraints
filters = []
# Domain restriction
filters.append(DomainFilter(allowed_domains=["docs.example.com"]))
# URL pattern include: only follow /api/* paths
filters.append(URLPatternFilter(patterns=["/api/*"]))
# URL pattern exclude: skip /api/internal/*
filters.append(URLPatternFilter(patterns=["/api/internal/*"], reverse=True))

strategy = BFSDeepCrawlStrategy(
    max_depth=3,
    max_pages=100,
    include_external=False,
    filter_chain=FilterChain(filters),
)

# Pass strategy via CrawlerRunConfig
run_cfg = CrawlerRunConfig(
    deep_crawl_strategy=strategy,
    verbose=False,  # CRITICAL
)

# arun() returns List[CrawlResult] when deep_crawl_strategy is set
results: list[CrawlResult] = await crawler.arun(url="https://docs.example.com", config=run_cfg)

for result in results:
    depth = result.metadata.get("depth", 0)
    parent = result.metadata.get("parent_url")
    print(f"Depth {depth}: {result.url} (from {parent})")
```

### Sitemap XML Parsing

```python
# Source: stdlib xml.etree.ElementTree + httpx (transitive dep of crawl4ai)
import gzip
import httpx
import xml.etree.ElementTree as ET

SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

async def _fetch_sitemap_urls(sitemap_url: str) -> list[str]:
    """Recursively fetch and parse sitemap XML, returning all <loc> URLs.

    Handles:
    - Regular sitemaps (<urlset> with <url><loc>)
    - Sitemap indexes (<sitemapindex> with <sitemap><loc>)
    - Gzipped sitemaps (.xml.gz)
    """
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        resp = await client.get(sitemap_url)
        resp.raise_for_status()

    content = resp.content
    if sitemap_url.endswith(".gz"):
        content = gzip.decompress(content)

    root = ET.fromstring(content)

    # Check if this is a sitemap index
    sub_sitemaps = root.findall("sm:sitemap/sm:loc", SITEMAP_NS)
    if sub_sitemaps:
        urls = []
        for loc_elem in sub_sitemaps:
            sub_urls = await _fetch_sitemap_urls(loc_elem.text.strip())
            urls.extend(sub_urls)
        return urls

    # Regular sitemap
    return [loc.text.strip() for loc in root.findall("sm:url/sm:loc", SITEMAP_NS)]
```

### Result Formatting Pattern (all three tools)

```python
# Established pattern from existing tools, adapted for multi-result output
def _format_multi_results(results: list[CrawlResult]) -> str:
    """Format multiple CrawlResults into a structured string for Claude."""
    parts = []
    successes = [r for r in results if r.success]
    failures = [r for r in results if not r.success]

    parts.append(f"Crawled {len(successes)} of {len(results)} URLs successfully.\n")

    for result in successes:
        md = result.markdown
        content = (md.fit_markdown or md.raw_markdown) if md else ""
        depth_info = ""
        if result.metadata and "depth" in result.metadata:
            depth_info = f" (depth: {result.metadata['depth']})"
        parts.append(f"## {result.url}{depth_info}\n\n{content}\n")

    if failures:
        parts.append(f"\n## Failed URLs ({len(failures)})\n")
        for result in failures:
            parts.append(f"- {result.url}: {result.error_message}\n")

    return "\n".join(parts)
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Manual asyncio.gather() + arun() loops | `arun_many()` with dispatcher | crawl4ai 0.4+ | Built-in concurrency control, rate limiting, memory tracking |
| Custom BFS queue implementation | `BFSDeepCrawlStrategy` in `CrawlerRunConfig` | crawl4ai 0.5+ | Native integration with arun(), automatic dedup, depth tracking |
| `arun_many()` returns `List[CrawlResult]` only | Returns `Union[List[CrawlResult], AsyncGenerator]` based on `config.stream` | crawl4ai 0.8.0 | Streaming mode available but batch is default |
| No crash recovery in deep crawl | `resume_state` + `on_state_change` callbacks | crawl4ai 0.8.0 | Can checkpoint and resume interrupted crawls (not needed for MCP v1) |

**Deprecated/outdated:**
- `arun_many(verbose=True, ...)` legacy kwargs: Still accepted for backward compat but ignored. Use `CrawlerRunConfig` instead.
- Manual `asyncio.Semaphore` concurrency wrappers: Replaced by `SemaphoreDispatcher`.

## Open Questions

1. **Should deep_crawl bypass build_run_config or extend KNOWN_KEYS?**
   - What we know: `build_run_config()` strips unknown keys. `deep_crawl_strategy` is not in `KNOWN_KEYS`.
   - What's unclear: Whether adding it to `_PER_CALL_KEYS` is cleaner than bypassing the profile merge entirely (as Phase 4 extraction tools did).
   - Recommendation: Add `"deep_crawl_strategy"` to `_PER_CALL_KEYS` in profiles.py. Unlike extraction tools, deep_crawl benefits from profile merging (the crawl config for each page should respect profiles). This is a one-line change.

2. **Should crawl_many reuse the same headers/cookies hook pattern from crawl_url?**
   - What we know: `_crawl_with_overrides()` injects headers/cookies via Playwright hooks for single-URL crawls. `arun_many()` manages its own Playwright sessions.
   - What's unclear: Whether hooks set on `crawler.crawler_strategy` persist across `arun_many()`'s internal parallel sessions.
   - Recommendation: Test empirically during implementation. If hooks work, reuse the pattern. If not, pass headers/cookies as `CrawlerRunConfig` attributes (check if 0.8.0 supports this for `arun_many` even if not for single `arun`). Worst case: skip per-call headers/cookies for `crawl_many` in v1 and document the limitation.

3. **What is the internal dispatcher used by BFSDeepCrawlStrategy's per-level arun_many calls?**
   - What we know: BFS strategy calls `crawler.arun_many(urls=urls, config=batch_config)` per level without specifying a dispatcher, so it uses the default `MemoryAdaptiveDispatcher`.
   - What's unclear: Whether this causes unwanted rate limiting between BFS levels.
   - Recommendation: Accept the default for now. The `MemoryAdaptiveDispatcher`'s rate limiter applies per-domain, which is reasonable for deep crawling (hitting the same domain repeatedly). If performance is a concern, we can investigate injecting a custom dispatcher in a future iteration.

4. **Should crawl_sitemap have a max_urls parameter?**
   - What we know: Large sitemaps can have 50,000+ URLs. Crawling all of them without a limit could be problematic.
   - What's unclear: Whether this is the agent's responsibility (it can always pass fewer URLs) or the tool's responsibility.
   - Recommendation: Add a `max_urls` parameter (default high, e.g., 500) that limits how many sitemap URLs are crawled. The agent can override. This is consistent with the "expose controls, let agent decide" philosophy.

## Sources

### Primary (HIGH confidence)
- Installed crawl4ai 0.8.0 source: `async_webcrawler.py` -- `arun_many()` method signature, dispatcher integration, return types
- Installed crawl4ai 0.8.0 source: `deep_crawling/bfs_strategy.py` -- `BFSDeepCrawlStrategy` full implementation, `_arun_batch`, `link_discovery`, `max_pages` enforcement
- Installed crawl4ai 0.8.0 source: `deep_crawling/filters.py` -- `FilterChain`, `DomainFilter`, `URLPatternFilter` implementations with parameter signatures
- Installed crawl4ai 0.8.0 source: `deep_crawling/base_strategy.py` -- `DeepCrawlDecorator` showing how `arun()` intercepts deep crawl config
- Installed crawl4ai 0.8.0 source: `async_dispatcher.py` -- `SemaphoreDispatcher`, `MemoryAdaptiveDispatcher`, `RateLimiter`, `BaseDispatcher` implementations
- Installed crawl4ai 0.8.0 source: `components/crawler_monitor.py` -- confirmed `Console()` and `rich.live.Live` usage (stdout risk)
- Installed crawl4ai 0.8.0 source: `async_configs.py` -- `CrawlerRunConfig.deep_crawl_strategy`, `stream`, `clone()` parameters
- Context7 `/websites/crawl4ai` -- `arun_many()` API docs, dispatcher examples, deep crawl strategy configuration
- Context7 `/unclecode/crawl4ai` -- BFS strategy examples, FilterChain/DomainFilter usage
- Existing codebase: `server.py` -- `crawl_url`, `_crawl_with_overrides`, `_format_crawl_error` patterns
- Existing codebase: `profiles.py` -- `build_run_config`, `KNOWN_KEYS`, `_PER_CALL_KEYS`

### Secondary (MEDIUM confidence)
- Perplexity reasoning: confirmed BFSDeepCrawlStrategy parameters, dispatcher hierarchy, FilterChain concept
- Perplexity search: SemaphoreDispatcher vs MemoryAdaptiveDispatcher tradeoffs

### Tertiary (LOW confidence)
- None

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all APIs verified against installed 0.8.0 source code, not just documentation
- Architecture patterns: HIGH -- patterns derived from actual crawl4ai implementation; filter/strategy classes inspected line-by-line
- Pitfalls: HIGH -- CrawlerMonitor stdout risk confirmed by reading Rich import in source; BFS mutable state confirmed by reading `_pages_crawled` tracking; namespace issue is standard XML parsing knowledge
- Code examples: HIGH -- all examples based on verified API signatures from installed source

**Research date:** 2026-02-21
**Valid until:** 2026-03-23 (30 days -- crawl4ai is pinned to 0.8.0; findings stable until pin changes)
**Next research trigger:** If `crawl4ai` publishes a new minor version, re-validate `arun_many()` dispatcher API and `BFSDeepCrawlStrategy` constructor signature.
