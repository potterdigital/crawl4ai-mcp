# Feature Research

**Domain:** MCP server wrapping crawl4ai for Claude Code
**Researched:** 2026-02-19
**Confidence:** HIGH

## Feature Landscape

### Table Stakes (Users Expect These)

Features users assume exist. Missing these = product feels incomplete.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Single URL crawl to markdown | Core value prop -- "give me a URL, get clean markdown" | LOW | `AsyncWebCrawler.arun()` with default config returns `result.markdown.raw_markdown`. Every competing MCP server has this. |
| JS rendering via Playwright | Many modern sites are SPAs; without JS rendering the markdown is empty/garbage | LOW | Playwright is crawl4ai's default engine. `BrowserConfig(headless=True)` is the baseline. Already built in. |
| CSS selector scoping | Users need to target specific page regions (article body, product listing) to avoid nav/footer noise | LOW | `CrawlerRunConfig(css_selector="main.content")` -- single parameter. |
| Tag/element exclusion | Remove nav, footer, sidebar, ads from output | LOW | `CrawlerRunConfig(excluded_tags=["nav", "footer", "aside"])` -- list parameter. |
| Error handling & status codes | Users need to know if crawl failed, why, and the HTTP status | LOW | `CrawlResult.success`, `.status_code`, `.error_message` are already on the result object. Surface them in tool response. |
| Cache control | Users must be able to bypass cache for fresh content or use cache for speed | LOW | `CrawlerRunConfig(cache_mode=CacheMode.BYPASS)` -- expose as simple enum: `"bypass"`, `"enabled"`, `"disabled"`. |
| Timeout configuration | Pages that take too long should fail gracefully, not hang the MCP server | LOW | `CrawlerRunConfig(page_timeout=30000)` in ms. Expose as seconds in the tool schema. |
| Link extraction | Users want internal/external links from crawled pages for further analysis | LOW | `CrawlResult.links` returns `{"internal": [...], "external": [...]}`. Include in response metadata. |
| Metadata extraction | Page title, description, OG tags -- basic page intelligence | LOW | `CrawlResult.metadata` -- already extracted. Include in response. |

### Differentiators (Competitive Advantage)

Features that set the product apart. Not required, but valuable.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| LLM-powered structured extraction | Define a JSON schema (Pydantic-style), get structured data back -- the killer feature of crawl4ai | MEDIUM | `LLMExtractionStrategy` with `llm_config`, `schema`, `instruction`, `extraction_type="schema"`. Requires user's LLM API key. Expose as a dedicated tool (not a parameter on the crawl tool). |
| CSS/JSON extraction (no LLM) | Structured data extraction without LLM costs using CSS selectors -- fast, free, deterministic | MEDIUM | `JsonCssExtractionStrategy(schema)` where schema defines `baseSelector` and `fields` with CSS selectors. Good for product listings, article feeds, tables. Separate tool. |
| Deep crawl (BFS link-following) | Crawl an entire site section by following links to configurable depth -- crucial for documentation ingestion | MEDIUM | `BFSDeepCrawlStrategy(max_depth=N, max_pages=M, include_external=False)` via `CrawlerRunConfig(deep_crawl_strategy=...)`. Needs `max_depth` and `max_pages` caps for safety. |
| Deep crawl with filters & scorers | Smart crawling that prioritizes relevant URLs using domain filters, URL patterns, keyword scoring, freshness scoring | HIGH | `FilterChain` + `CompositeScorer` with `DomainFilter`, `URLPatternFilter`, `KeywordRelevanceScorer`, `FreshnessScorer`, `PathDepthScorer`. Very powerful but complex config surface. |
| Parallel/batch crawling | Crawl multiple URLs concurrently with adaptive concurrency | MEDIUM | `arun_many()` with `SemaphoreDispatcher(semaphore_count=N)` or `MemoryAdaptiveDispatcher`. Essential for batch documentation ingestion. |
| Preset profiles (fast/js-heavy/stealth) | One-word config instead of 10+ parameters -- dramatically reduces tool call complexity | LOW | Server-side profile definitions that expand to `BrowserConfig` + `CrawlerRunConfig` presets. This is our design, not a crawl4ai feature. |
| JS execution hooks | Click buttons, dismiss modals, trigger infinite scroll, interact with page before extraction | MEDIUM | `CrawlerRunConfig(js_code="...", wait_for="css:.loaded")`. String-based JS is straightforward but needs careful escaping in MCP tool params. |
| Authenticated crawling (cookies/headers) | Access gated content by injecting session cookies or auth headers | MEDIUM | `BrowserConfig(cookies=[...], headers={...})`. Security-sensitive -- must handle credentials carefully. Users pass cookies, not passwords. |
| Content filtering (fit_markdown) | AI-ready markdown that strips boilerplate using heuristic pruning or BM25 relevance scoring | MEDIUM | `PruningContentFilter(threshold=0.48)` or `BM25ContentFilter(user_query="...", bm25_threshold=1.0)` via `DefaultMarkdownGenerator(content_filter=...)`. Produces `result.markdown.fit_markdown`. |
| Screenshot capture | Visual snapshot of rendered page -- useful for debugging, visual QA, page structure analysis | LOW | `CrawlerRunConfig(screenshot=True)` returns base64 PNG in `result.screenshot`. |
| PDF generation | Save rendered page as PDF | LOW | `CrawlerRunConfig(pdf=True)` returns PDF bytes in `result.pdf`. |
| Stealth/anti-detection mode | Bypass bot detection (Cloudflare, DataDome) for sites that block scrapers | MEDIUM | `BrowserConfig(enable_stealth=True, user_agent_mode="random", navigator_override=True)`. The `magic=True` flag enables behavioral simulation. |
| Update checker tool | Let Claude check if crawl4ai is outdated and suggest upgrade commands | LOW | `pip index versions crawl4ai` + compare with installed version. Simple tool, high utility for maintenance. |
| Sitemap-based crawling | Discover all pages via sitemap.xml and crawl selectively | MEDIUM | Parse sitemap XML, feed URLs to `arun_many()`. Not a built-in crawl4ai feature but a natural MCP tool built on top. |
| Session persistence | Maintain browser session across multiple crawl calls for multi-step workflows | MEDIUM | `CrawlerRunConfig(session_id="my_session")` keeps browser context alive. Useful for login-then-crawl flows. |
| Robots.txt respect | Ethical crawling that honors site policies | LOW | `CrawlerRunConfig(check_robots_txt=True)`. Should be enabled by default with override option. |

### Anti-Features (Commonly Requested, Often Problematic)

Features that seem good but create problems.

| Feature | Why Requested | Why Problematic | Alternative |
|---------|---------------|-----------------|-------------|
| Full hook system exposure | "Let me write custom Python hooks for on_browser_created, before_goto, etc." | Hooks require executable Python code strings, which is a security nightmare in MCP context (arbitrary code execution vulnerability was CVE'd in crawl4ai pre-0.8.0). Hooks also require Playwright API knowledge the LLM may hallucinate. | Expose specific hook outcomes as parameters: `js_code` for page interaction, `wait_for` for load conditions, `cookies`/`headers` for auth. Cover 95% of hook use cases without arbitrary code. |
| Expose every CrawlerRunConfig parameter | "Give users full control over all 40+ parameters" | Overwhelms the LLM's tool schema parsing. MCP tools work best with focused, well-described parameters. A tool with 40 params gets misused or ignored. | Use preset profiles for common combos. Expose ~10 most-used params as tool overrides. Advanced users edit config directly. |
| Real-time streaming of crawl results | "Stream partial results as pages are crawled" | MCP tool calls are request/response, not streaming. Attempting to stream via SSE or chunked responses adds complexity with no MCP-native support. | Return complete results. For long crawls, use a status/polling pattern: start crawl, return job ID, poll for results. |
| Proxy rotation/pool management | "Manage a pool of rotating proxies automatically" | Proxy management is an ops concern, not a crawl tool concern. Adds significant complexity (health checking, rotation logic, credential management) with narrow use cases. | Accept a single `proxy` URL parameter. Users manage their own proxy service (BrightData, ScraperAPI, etc.) and pass the endpoint URL. |
| Cosine clustering extraction strategy | "Use embedding-based clustering for extraction" | `CosineStrategy` requires embedding models, adds heavy dependencies, and has niche applicability. LLM extraction covers this use case better. | Use `LLMExtractionStrategy` for semantic extraction or `JsonCssExtractionStrategy` for structured extraction. |
| Docker/self-hosted crawl4ai server mode | "Run crawl4ai as a separate Docker service and have the MCP server call its API" | Adds deployment complexity (Docker, networking, port management) for a local dev tool. The MCP server should embed crawl4ai directly. | Direct Python import of crawl4ai as a library dependency. Single process, no networking overhead. |
| Browser choice (Firefox/WebKit) | "Let users pick Firefox or WebKit instead of Chromium" | Chromium is the only fully-tested, well-supported browser in Playwright. Firefox/WebKit have edge cases with JS rendering. Adds testing surface for no real gain. | Default to Chromium. Only expose browser choice if specific user feedback demands it. |
| PDF output as a core feature | "Generate PDFs for every crawl" | PDFs are large, hard to return in MCP responses, and rarely needed. Screenshot covers visual inspection needs. | Keep `screenshot=True` as the visual output option. PDF available as an advanced override if needed but not a primary tool. |

## Feature Dependencies

```
[JS Rendering (Playwright)]
    └──enables──> [Single URL Crawl]
                      └──enables──> [Deep Crawl (BFS)]
                      │                 └──requires──> [Link Extraction]
                      │                 └──enhances──> [Filters & Scorers]
                      └──enables──> [Parallel Batch Crawl]
                      └──enables──> [LLM Structured Extraction]
                      │                 └──requires──> [User's LLM API Key]
                      └──enables──> [CSS/JSON Extraction]
                      └──enables──> [Content Filtering (fit_markdown)]

[Preset Profiles]
    └──configures──> [BrowserConfig + CrawlerRunConfig]
                         └──enables──> [All crawl features]

[Session Persistence]
    └──enables──> [Authenticated Crawling]
                      └──requires──> [Cookies/Headers]

[Sitemap Crawling]
    └──requires──> [Parallel Batch Crawl]

[Update Checker]
    └──independent──> (no dependencies)

[Stealth Mode]
    └──enhances──> [Single URL Crawl]
    └──enhances──> [Deep Crawl]
```

### Dependency Notes

- **Deep Crawl requires Link Extraction:** BFS strategy follows extracted links to discover pages. Link extraction is a byproduct of the base crawl, so this is already satisfied.
- **LLM Extraction requires API Key:** The `LLMExtractionStrategy` calls an external LLM (OpenAI, Ollama, etc.). The MCP server must accept and forward API credentials without storing them.
- **Authenticated Crawling requires Session Persistence:** Login-then-crawl workflows need `session_id` to maintain browser state between the login step and content crawling step.
- **Preset Profiles enhance all crawl tools:** Profiles are a convenience layer. Every tool should accept a `profile` parameter that expands to predefined `BrowserConfig` + `CrawlerRunConfig` values, with per-call overrides taking precedence.
- **Sitemap Crawling requires Batch Crawl:** Sitemaps produce URL lists that feed into `arun_many()` for parallel processing.

## MVP Definition

### Launch With (v1)

Minimum viable product -- what's needed to validate the concept.

- [ ] **`crawl` tool** -- Single URL to clean markdown with CSS selector, excluded tags, cache control, timeout. This is the atomic operation everything else builds on.
- [ ] **`crawl_many` tool** -- Multiple URLs in parallel with concurrency control. Needed for batch documentation ingestion.
- [ ] **Preset profiles** -- `fast` (headless, text_mode, no images), `js-heavy` (wait for network idle, simulate_user), `stealth` (enable_stealth, random UA, navigator_override). Reduces parameter complexity for 90% of use cases.
- [ ] **Error handling** -- Surface `success`, `status_code`, `error_message` clearly in every response.
- [ ] **`check_updates` tool** -- Compare installed vs latest crawl4ai version, return upgrade command.

### Add After Validation (v1.x)

Features to add once core is working.

- [ ] **`extract_structured` tool (LLM)** -- LLM-powered extraction with user-provided schema and instruction. Trigger: users want structured data, not just markdown.
- [ ] **`extract_css` tool** -- CSS-based structured extraction without LLM costs. Trigger: users want structured data for known page layouts.
- [ ] **`deep_crawl` tool** -- BFS link-following with depth/page limits. Trigger: users want to ingest entire doc sites.
- [ ] **Authenticated crawling** -- Cookie/header injection for gated content. Trigger: users need to crawl internal wikis or authenticated sites.
- [ ] **Content filtering** -- `PruningContentFilter` and `BM25ContentFilter` for fit_markdown output. Trigger: raw markdown is too noisy for LLM consumption.
- [ ] **Screenshot tool** -- Capture rendered page as image. Trigger: users want visual page inspection.

### Future Consideration (v2+)

Features to defer until product-market fit is established.

- [ ] **Sitemap-based crawling** -- Parse and selectively crawl from sitemap.xml. Why defer: can be approximated with deep_crawl.
- [ ] **Deep crawl with filters/scorers** -- Advanced URL prioritization. Why defer: complex config surface, niche use case, deep_crawl with max_depth/max_pages covers 80%.
- [ ] **Session persistence across tool calls** -- Multi-step workflows (login then crawl). Why defer: requires state management in the MCP server, adds complexity.
- [ ] **Stealth mode as a dedicated profile** -- Beyond the preset, full anti-detection config. Why defer: most crawling targets don't need it.

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---------|------------|---------------------|----------|
| Single URL crawl to markdown | HIGH | LOW | P1 |
| JS rendering (Playwright) | HIGH | LOW (built-in) | P1 |
| CSS selector scoping | HIGH | LOW | P1 |
| Tag/element exclusion | MEDIUM | LOW | P1 |
| Error handling & status codes | HIGH | LOW | P1 |
| Cache control | MEDIUM | LOW | P1 |
| Timeout configuration | MEDIUM | LOW | P1 |
| Link extraction | MEDIUM | LOW | P1 |
| Metadata extraction | MEDIUM | LOW | P1 |
| Preset profiles | HIGH | LOW | P1 |
| Parallel batch crawling | HIGH | MEDIUM | P1 |
| Update checker | MEDIUM | LOW | P1 |
| Robots.txt respect | MEDIUM | LOW | P1 |
| LLM structured extraction | HIGH | MEDIUM | P2 |
| CSS/JSON extraction | MEDIUM | MEDIUM | P2 |
| Deep crawl (BFS) | HIGH | MEDIUM | P2 |
| Content filtering (fit_markdown) | MEDIUM | MEDIUM | P2 |
| JS execution hooks | MEDIUM | LOW | P2 |
| Authenticated crawling | MEDIUM | MEDIUM | P2 |
| Screenshot capture | LOW | LOW | P2 |
| Sitemap crawling | MEDIUM | MEDIUM | P3 |
| Deep crawl filters/scorers | LOW | HIGH | P3 |
| Session persistence | LOW | HIGH | P3 |
| PDF generation | LOW | LOW | P3 |

## MCP Tool Design Recommendations

### Tool Architecture

Design follows the "less is more" MCP pattern: fewer, focused tools with clear names beat many overlapping tools.

| Tool Name | Purpose | Key Parameters |
|-----------|---------|----------------|
| `crawl` | Single URL to markdown | `url`, `css_selector`, `excluded_tags`, `wait_for`, `profile`, `timeout` |
| `crawl_many` | Parallel batch crawl | `urls`, `css_selector`, `max_concurrent`, `profile` |
| `deep_crawl` | BFS link-following crawl | `url`, `max_depth`, `max_pages`, `include_external`, `profile` |
| `extract_structured` | LLM-powered extraction | `url`, `schema`, `instruction`, `llm_provider`, `api_key` |
| `extract_css` | CSS-based extraction | `url`, `schema` (baseSelector + fields) |
| `check_updates` | Version check | (none) |

### Preset Profile Definitions

| Profile | BrowserConfig | CrawlerRunConfig | Use Case |
|---------|--------------|-----------------|----------|
| `fast` | `headless=True, text_mode=True, light_mode=True` | `cache_mode=CacheMode.ENABLED, word_count_threshold=10, excluded_tags=["script","style","nav","footer"]` | Static pages, documentation, articles |
| `js-heavy` | `headless=True` | `wait_for="networkidle", page_timeout=60000, simulate_user=True, remove_overlay_elements=True` | SPAs, React/Vue/Angular sites, dynamic content |
| `stealth` | `headless=True, enable_stealth=True, user_agent_mode="random", navigator_override=True` | `simulate_user=True, magic=True` | Bot-protected sites, Cloudflare/DataDome |

### Parameter Design Principles

1. **Profiles first, overrides second:** Every tool accepts a `profile` param (fast/js-heavy/stealth/default). Any explicit parameter overrides the profile's value.
2. **Flatten, don't nest:** MCP tools work best with flat parameter lists. Don't expose `BrowserConfig` as a nested object -- expose the 5-10 params users actually change.
3. **Sensible defaults:** Default profile is `fast` for `crawl`, `js-heavy` for `deep_crawl`. Users who pass no config still get good results.
4. **Cap dangerous params:** `max_pages` capped at 100, `max_depth` capped at 5, `timeout` capped at 120s. Prevents runaway crawls.
5. **Return structured results:** Every tool returns `{ success, url, status_code, markdown, links, metadata, error_message }`. Consistent shape across all tools.

## Competitor Feature Analysis

| Feature | sadiuysal/crawl4ai-mcp | BjornMelin/crawl4ai-mcp | Apify crawl4ai MCP | Our Approach |
|---------|----------------------|------------------------|--------------------|--------------|
| Single URL crawl | `scrape` tool | `crawl` tool | Yes | `crawl` -- cleaner name, preset profiles |
| Multi-page crawl | `crawl` (BFS) | `crawl` (depth/pages) | Yes (link following) | `deep_crawl` -- separate tool, explicit naming |
| Sitemap crawl | `crawl_sitemap` | No | No | Defer to v2+ |
| Batch parallel | No (sequential) | No | No | `crawl_many` -- differentiator via `arun_many()` |
| LLM extraction | No | `extract` (CSS/LLM) | Yes (LLM) | `extract_structured` -- dedicated, schema-driven |
| CSS extraction | No | `extract` (CSS/LLM) | Yes (CSS/XPath) | `extract_css` -- separate from LLM extraction |
| Preset profiles | No | No | No | Yes -- major differentiator for DX |
| Update checker | No | No | No | Yes -- maintenance convenience |
| Stealth mode | No | No | No | Yes -- via `stealth` profile |
| Content filtering | No | No | No | Yes (v1.x) -- `fit_markdown` via filters |
| Auth support | Cookies in config | OAuth/API key (server auth) | No | Cookie/header injection per-crawl |

### Key Differentiation from Existing MCP Servers

1. **Preset profiles** -- No existing crawl4ai MCP server offers named presets. This is the single biggest DX improvement.
2. **True parallel batch crawling** -- Existing servers crawl sequentially. Using `arun_many()` with `SemaphoreDispatcher` enables real concurrency.
3. **Separate extraction tools** -- Existing servers either skip extraction or lump CSS+LLM into one tool. Separate tools with clear schemas reduce errors.
4. **Update management** -- No existing server helps users maintain their crawl4ai installation.
5. **Content filtering** -- `fit_markdown` via `PruningContentFilter`/`BM25ContentFilter` produces cleaner, more LLM-ready output than any competitor.

## Sources

- crawl4ai official documentation: https://docs.crawl4ai.com (HIGH confidence)
- crawl4ai Context7 docs: /websites/crawl4ai, /unclecode/crawl4ai (HIGH confidence)
- crawl4ai PyPI: https://pypi.org/project/Crawl4AI/ (HIGH confidence)
- crawl4ai GitHub: https://github.com/unclecode/crawl4ai (HIGH confidence)
- sadiuysal/crawl4ai-mcp-server: https://github.com/sadiuysal/crawl4ai-mcp-server (MEDIUM confidence)
- BjornMelin/crawl4ai-mcp-server: https://github.com/BjornMelin/crawl4ai-mcp-server (MEDIUM confidence)
- Apify crawl4ai MCP server: https://apify.com/mcp/crawl4ai-mcp-server (MEDIUM confidence)
- MCP design patterns: https://www.klavis.ai/blog/less-is-more-mcp-design-patterns-for-ai-agents (MEDIUM confidence)
- MCP specification: https://modelcontextprotocol.io/specification/2025-06-18/server/tools (HIGH confidence)

---
*Feature research for: crawl4ai MCP server for Claude Code*
*Researched: 2026-02-19*
