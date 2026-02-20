# Pitfalls Research

**Domain:** Python MCP Server wrapping crawl4ai for Claude Code
**Researched:** 2026-02-19
**Confidence:** HIGH (verified against crawl4ai docs, GitHub issues, MCP Python SDK docs, and community reports)

## Critical Pitfalls

### Pitfall 1: Browser Process Leaks and Memory Exhaustion

**What goes wrong:**
crawl4ai's AsyncWebCrawler spawns Playwright-managed Chromium processes that are not reliably cleaned up. Repeated crawl requests cause Chrome processes to accumulate, steadily increasing memory usage until the server crashes. This is a confirmed, open issue across crawl4ai versions 0.6.0 through 0.7.2+ (GitHub issues #1256, #1608, #361, #943). Even v0.7.3, which claims some memory leak fixes, does not fully resolve it.

**Why it happens:**
- crawl4ai holds references to crawl results (HTML content not cleared in internal dispatchers)
- Playwright browser processes are not terminated after crawl completion
- When using the crawler outside the `async with` context manager, cleanup never happens
- The internal `crawler_pool.py` monitors host memory via `psutil.virtual_memory()` instead of process-specific usage, so it misjudges when to reclaim resources

**How to avoid:**
1. Always use `async with AsyncWebCrawler() as crawler:` -- never instantiate without the context manager
2. Manage a single, long-lived AsyncWebCrawler instance across the entire MCP server lifetime via the FastMCP lifespan pattern, rather than creating/destroying per request
3. After processing each crawl result, explicitly clear large fields: `result.html = ""; result.fit_html = ""`
4. Set BrowserConfig args: `["--disable-gpu", "--disable-dev-shm-usage", "--no-sandbox"]`
5. Limit concurrency to 2-3 simultaneous crawls maximum
6. Implement a periodic health check that monitors memory and restarts the crawler if it exceeds a threshold (e.g., 500MB)

**Warning signs:**
- MCP server response times degrading over a session
- System memory climbing steadily with each crawl request
- `ps aux | grep chrom` showing accumulating browser processes
- Server becoming unresponsive after 20-50 crawl requests

**Phase to address:**
Phase 1 (Foundation) -- browser lifecycle management must be correct from day one. This is the single most important architectural decision.

---

### Pitfall 2: stdout Corruption from Print Statements or Library Logging

**What goes wrong:**
MCP stdio transport uses stdout for JSON-RPC message passing. Any `print()` statement, library debug output, or logging directed to stdout corrupts the protocol stream, causing silent timeouts, connection drops, or garbled responses. Claude Code will report the MCP server as disconnected or unresponsive.

**Why it happens:**
- Python's default `print()` goes to stdout
- crawl4ai's `verbose=True` mode outputs to stdout
- Third-party libraries (Playwright, httpx, etc.) may log to stdout by default
- Python's `logging` module defaults to stderr, but some configurations redirect to stdout
- Even a single stray print statement breaks the entire transport

**How to avoid:**
1. Configure all logging exclusively to stderr: `logging.basicConfig(stream=sys.stderr)`
2. Never use `print()` anywhere in the server code -- enforce with a linting rule
3. Set `verbose=False` in all crawl4ai configs (BrowserConfig and CrawlerRunConfig)
4. Redirect Playwright's own logging to stderr
5. Test the server manually by piping stdin/stdout and verifying only valid JSON-RPC appears on stdout
6. Use the MCP SDK's built-in `ctx.info()` / `ctx.debug()` logging methods which route through the protocol correctly

**Warning signs:**
- Claude Code shows "MCP server disconnected" or times out after 60 seconds
- Server process starts but no tools appear in Claude Code
- Intermittent connection drops that correlate with certain crawl operations

**Phase to address:**
Phase 1 (Foundation) -- this must be enforced from the first line of code. Add a pre-commit check that greps for `print(` in server code.

---

### Pitfall 3: LLM Extraction Cost Blowup

**What goes wrong:**
Using `LLMExtractionStrategy` for every crawl request sends page content to an LLM API, incurring per-token charges. A single page can cost $0.001-0.01, but deep crawls or batch operations multiply this to $0.30+ per request. An MCP tool that defaults to LLM extraction can silently run up hundreds of dollars in API costs during a Claude Code session, especially if Claude decides to crawl multiple pages.

**Why it happens:**
- LLM extraction is the "default cool feature" so developers make it the primary path
- HTML bloat (scripts, styles, boilerplate) inflates token counts dramatically
- Deep crawls multiply the per-page cost by every discovered page
- No built-in spending cap or cost estimation in crawl4ai
- Claude Code may invoke the crawl tool repeatedly in a loop without the user realizing the cost

**How to avoid:**
1. Make LLM extraction opt-in, not the default. Default to markdown output or CSS-based extraction
2. Use `input_format="fit_markdown"` instead of `"html"` to dramatically reduce token consumption
3. Set conservative `chunk_token_threshold` (e.g., 2000) and `overlap_rate=0.0` unless overlap is needed
4. Prefer `JsonCssExtractionStrategy` or `RegexExtractionStrategy` for structured/predictable content -- these are free and 100x faster
5. Implement a cost estimation tool that shows approximate cost before executing LLM extraction
6. Set `max_tokens` in `extra_args` to cap per-chunk LLM output
7. Never combine LLM extraction with deep crawling -- the cost scales multiplicatively
8. Use `gpt-4o-mini` or similar cheap models, not `gpt-4` for extraction

**Warning signs:**
- `llm_strategy.show_usage()` showing unexpectedly high token counts
- API billing alerts from OpenAI/Anthropic
- Crawl operations taking 10+ seconds (LLM round-trip per chunk)

**Phase to address:**
Phase 2 (Extraction features) -- but the architecture must support non-LLM extraction from Phase 1. LLM extraction should be a separate, clearly-marked tool with cost warnings in the description.

---

### Pitfall 4: Deep Crawl Runaway (Infinite Loops / Pagination Traps)

**What goes wrong:**
crawl4ai's `BFSDeepCrawlStrategy` and `DFSDeepCrawlStrategy` have `max_pages` defaulting to infinity. Without explicit limits, a deep crawl on a site with infinite pagination, calendar archives, or dynamic URL parameters will crawl indefinitely, consuming memory, bandwidth, and potentially getting the server IP blocked.

**Why it happens:**
- `max_pages` is not required and defaults to unlimited
- `max_depth` alone is insufficient -- a single depth level can contain thousands of pages
- Pagination links (e.g., `/page/1`, `/page/2`, ..., `/page/99999`) exist at the same depth
- Calendar/archive pages generate infinite date-based URLs
- DFS strategy is especially vulnerable -- it follows deep chains before exploring breadth
- Query parameters create URL variations that are treated as distinct pages

**How to avoid:**
1. Always set both `max_depth` and `max_pages` in every deep crawl strategy configuration
2. Enforce hard limits at the MCP tool level: `max_depth` capped at 3, `max_pages` capped at 50 by default
3. Use `include_external=False` to stay within the target domain
4. Implement `score_threshold=0.3` with a `KeywordRelevanceScorer` to skip low-relevance URLs
5. Add a timeout at the MCP tool level (e.g., 120 seconds max per deep crawl invocation)
6. Use `filter_chain` to exclude known pagination patterns (`/page/\d+`, `?page=`, calendar URLs)
7. Prefer BFS over DFS for most use cases -- it produces useful results at each depth level before going deeper
8. Start with conservative limits (max_depth=2, max_pages=20) and let users explicitly increase them

**Warning signs:**
- Crawl operation running for more than 60 seconds without returning
- Result count growing linearly without bound
- URLs in results showing incrementing numbers or date patterns
- Memory usage spiking during a crawl

**Phase to address:**
Phase 2 (Deep crawl feature) -- must be designed with hard limits from the start. The MCP tool parameters should enforce maximums.

---

### Pitfall 5: crawl4ai Breaking API Changes Between Versions

**What goes wrong:**
crawl4ai is under active development with frequent breaking changes. Version 0.5.0 (Feb 2025) broke dispatchers, scraping modes, and streaming. Subsequent releases moved `ProxyConfig`, renamed browser strategies, replaced `crawler_manager.py` with `crawler_pool.py`, and changed default behaviors. Pinning a version works short-term but accumulates security/feature debt; updating without testing breaks the server.

**Why it happens:**
- crawl4ai is pre-1.0 software with no stability guarantees
- Breaking changes are documented only in CHANGELOG.md, not in migration guides
- Import paths change between versions (e.g., `ProxyConfig` moved to `async_configs`)
- Default values change silently (e.g., `cache_mode` from aggressive to bypass)
- The Docker/API server architecture is being actively refactored

**How to avoid:**
1. Pin the exact crawl4ai version in requirements.txt (e.g., `crawl4ai==0.8.0`)
2. Write integration tests that exercise every crawl4ai API you use -- import paths, config construction, and result parsing
3. Create an abstraction layer between MCP tools and crawl4ai internals -- never call crawl4ai directly from tool handlers
4. Monitor the crawl4ai CHANGELOG.md (https://github.com/unclecode/crawl4ai/blob/main/CHANGELOG.md) for releases tagged "breaking"
5. Test version upgrades in isolation before deploying: `pip install crawl4ai==X.Y.Z` in a venv, run tests
6. Document which crawl4ai APIs you depend on so upgrade impact is assessable

**Warning signs:**
- `ImportError` or `ModuleNotFoundError` after a `pip install --upgrade`
- Tests passing locally but failing after dependency resolution changes
- New crawl4ai features in docs that use unfamiliar import paths
- CHANGELOG entries marked with "breaking"

**Phase to address:**
Phase 1 (Foundation) -- the abstraction layer and version pinning must be established from the start. Integration tests should be written alongside each feature.

---

### Pitfall 6: MCP Tool Descriptions That Confuse Claude

**What goes wrong:**
Poorly written MCP tool descriptions and parameter schemas cause Claude to invoke tools incorrectly, pass wrong parameter types, or choose the wrong tool entirely. This manifests as Claude repeatedly calling a crawl tool with bad URLs, passing a URL where a CSS selector is expected, or never discovering that a simpler extraction tool exists.

**Why it happens:**
- Tool descriptions are too vague ("crawl a website") without specifying what the tool returns or when to use it
- Parameter names are ambiguous (`content` could mean input or output; `type` is overloaded)
- Too many optional parameters overwhelm the LLM's ability to choose correctly
- No examples in descriptions showing expected usage patterns
- Single monolithic "crawl everything" tool instead of focused, single-purpose tools

**How to avoid:**
1. Write tool descriptions as action-oriented sentences: "Fetch a single web page and return its content as clean markdown. Use this for reading documentation pages or articles."
2. Include negative guidance: "Do NOT use this for extracting structured data -- use extract_structured_data instead"
3. Use descriptive parameter names: `target_url` not `url`, `css_selector` not `selector`, `max_crawl_depth` not `depth`
4. Add `Field(description="...")` annotations on every parameter with format examples
5. Keep each tool to 3-5 parameters maximum. Split complex operations into multiple focused tools
6. Design tools as a hierarchy: simple crawl (1 param) -> filtered crawl (3 params) -> deep crawl (5 params) -> LLM extraction (separate tool)
7. Test tools by asking Claude Code to use them and observing whether it picks the right one

**Warning signs:**
- Claude consistently passes wrong parameter types or combinations
- Claude uses a complex tool when a simpler one would suffice
- Claude asks the user to clarify how to use a tool
- Claude ignores available tools and tries to use WebFetch instead

**Phase to address:**
Phase 1 (Foundation) -- tool API design is the user-facing surface. Iterate on descriptions based on real Claude Code usage in Phase 1, refine in every subsequent phase.

---

## Technical Debt Patterns

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| Creating new AsyncWebCrawler per request | Simpler code, no lifecycle management | Browser startup cost per request (~2-3s), memory leaks from unclosed instances | Never -- always use a shared instance |
| Using `verbose=True` for debugging | See what crawl4ai is doing | stdout corruption kills MCP transport | Never in production -- use stderr logging |
| Defaulting to LLM extraction | Every crawl returns structured data | $0.01+ per page, 10x slower, API key dependency | Only when explicitly requested by user |
| No crawl4ai abstraction layer | Faster initial development | Every crawl4ai update breaks MCP tools directly | Only in throwaway prototypes |
| Hardcoding LLM provider/model | Simpler config | Lock-in, can't switch to cheaper models | Never -- use LLMConfig with env vars |
| Skipping robots.txt checks | Faster crawls | Legal risk, IP bans, ethical concerns | Never for a general-purpose tool |

## Integration Gotchas

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|------------------|
| crawl4ai + MCP stdio | Print statements or verbose logging corrupt stdout | Route ALL output to stderr; set `verbose=False` on all configs |
| crawl4ai + asyncio event loop | Calling synchronous Playwright methods from async context | Always use `AsyncWebCrawler` and `arun()`, never the sync API; use `asyncio.to_thread()` for any blocking operations |
| crawl4ai + LLM extraction | Passing raw HTML to LLM (massive token consumption) | Use `input_format="fit_markdown"` to reduce tokens by 60-80% |
| MCP tools + Claude Code | One giant tool with 15 parameters | Split into 3-4 focused tools with clear descriptions and 3-5 params each |
| crawl4ai sessions + authentication | Assuming cookies persist across AsyncWebCrawler instances | Use `session_id` parameter and keep the same crawler instance alive; cookies only persist within a session on the same instance |
| crawl4ai + deep crawl + extraction | Combining deep crawl with LLMExtractionStrategy | Never do this -- deep crawl produces N pages, each sent to the LLM. Use deep crawl for link discovery, then targeted extraction on specific pages |
| FastMCP lifespan + crawler | Not using lifespan for browser initialization | Use `@asynccontextmanager` lifespan to initialize AsyncWebCrawler at server startup and share it across all tool invocations |

## Performance Traps

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| New browser per crawl request | 2-3 second delay before each crawl returns | Shared AsyncWebCrawler instance via FastMCP lifespan | Immediately noticeable on first use |
| No concurrency limits on crawls | Memory exhaustion, Chromium crashes | Use `SemaphoreDispatcher(max_session_permit=3)` with rate limiting | After 5-10 concurrent crawl requests |
| Caching disabled (`CacheMode.BYPASS`) as default | Redundant crawls of same pages, slow responses | Default to `CacheMode.ENABLED`; let users opt into bypass | When same URLs are crawled repeatedly in a session |
| Screenshots enabled by default | Base64 screenshot data bloats every response | Only enable screenshots when explicitly requested | Every response is 2-10x larger than needed |
| Full HTML in results | Massive result payloads sent back through MCP | Return `markdown` or `fit_markdown` by default; offer HTML as opt-in | Every response saturates Claude's context window |
| No timeout on crawl operations | Single slow page blocks the entire server | Set `page_timeout` in CrawlerRunConfig and a wrapper timeout at the tool level | First encounter with a slow or hanging site |

## Security Mistakes

| Mistake | Risk | Prevention |
|---------|------|------------|
| Passing user-provided URLs without validation | SSRF -- crawling internal network addresses (localhost, 10.x.x.x, 169.254.x.x) | Validate URLs against blocklist of private IP ranges and localhost before crawling |
| Echoing cookies/auth tokens in crawl results | Credential leakage into Claude's context (visible in conversation) | Strip `Set-Cookie` headers and auth tokens from results before returning |
| Storing API keys in MCP tool config | Keys visible in `claude_desktop_config.json` or MCP config | Use environment variables (`env:OPENAI_API_KEY` pattern) |
| Executing arbitrary JavaScript from tool parameters | Code injection -- attacker-crafted prompts can run JS in browser context | Never allow raw JS execution from MCP parameters; whitelist specific actions |
| No rate limiting on crawl requests | Claude in a loop can overwhelm target sites, triggering IP bans | Implement per-domain rate limiting with `mean_delay=2.0` and `SemaphoreDispatcher` |

## "Looks Done But Isn't" Checklist

- [ ] **Browser cleanup:** Server appears to work but leaks Chrome processes -- verify with `ps aux | grep chrom` after 50+ requests
- [ ] **robots.txt compliance:** Crawl succeeds but `check_robots_txt` is `False` by default -- verify it's enabled or consciously disabled
- [ ] **Error propagation:** Tool returns success but `result.success` is `False` -- always check `result.success` and `result.error_message`
- [ ] **Session cleanup:** Authenticated crawls work but `kill_session()` is never called -- sessions accumulate as memory leaks
- [ ] **Deep crawl limits:** Deep crawl returns results but `max_pages` is unlimited -- verify hard limits are enforced at the tool level
- [ ] **LLM cost tracking:** Extraction works but no visibility into token usage -- call `strategy.show_usage()` and log costs
- [ ] **Cache behavior:** Crawl returns data but cache_mode is unclear -- verify whether results are fresh or stale
- [ ] **Timeout handling:** Tool appears to hang -- verify `page_timeout` is set and tool-level timeout is enforced
- [ ] **Content size:** Tool returns markdown but it's 100KB+ -- verify content is truncated or summarized before returning to Claude
- [ ] **URL validation:** Tool crawls URLs but accepts `file://` or `http://localhost` -- verify URL scheme and host validation

## Recovery Strategies

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| Browser memory leak crashes server | LOW | Restart MCP server; implement health check + auto-restart; add memory threshold monitoring |
| stdout corruption breaks MCP | LOW | Fix the print/logging statement; add linting rule; restart server |
| LLM extraction cost overrun | MEDIUM | Audit tool invocation logs; switch to CSS/regex extraction for predictable content; add cost caps |
| Deep crawl runaway | LOW | Kill the server process; add `max_pages` and timeout limits; restart |
| crawl4ai version break | MEDIUM | Pin version in requirements.txt; fix imports per CHANGELOG; add integration tests for all used APIs |
| Tool description confusion | LOW | Rewrite descriptions; test with Claude Code; iterate on parameter naming |
| Session/cookie leak | LOW | Add `kill_session()` calls in finally blocks; implement session TTL cleanup |
| SSRF via URL parameter | HIGH | Add URL validation immediately; audit past crawl logs for internal network access; block private ranges |

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase | Verification |
|---------|------------------|--------------|
| Browser process leaks | Phase 1: Foundation | Monitor Chrome process count after 50+ requests; memory stays under threshold |
| stdout corruption | Phase 1: Foundation | Linting rule passes; manual test confirms only JSON-RPC on stdout |
| crawl4ai API instability | Phase 1: Foundation | Abstraction layer exists; version pinned; integration tests cover all used APIs |
| Tool description confusion | Phase 1: Foundation | Claude Code correctly selects and invokes each tool in test scenarios |
| URL validation / SSRF | Phase 1: Foundation | Blocklist test passes for localhost, private IPs, file:// URLs |
| LLM extraction cost blowup | Phase 2: Extraction | Cost estimation visible before LLM calls; LLM extraction is opt-in, not default |
| Deep crawl runaway | Phase 2: Deep Crawling | Hard limits enforced; timeout kills long crawls; test with pagination-heavy sites |
| Authentication / session management | Phase 3: Auth Features | Sessions cleaned up in finally blocks; cookie state verified across multi-step crawls |
| Concurrency / rate limiting | Phase 2: Deep Crawling | SemaphoreDispatcher configured; per-domain delay enforced; no IP bans during testing |
| Content size overwhelming Claude | Phase 1: Foundation | All tool results truncated to reasonable size (e.g., 50KB max); markdown preferred over HTML |

## Sources

- crawl4ai GitHub Issues: Memory leaks (#1256, #1608, #361, #943) -- https://github.com/unclecode/crawl4ai/issues/1256
- crawl4ai CHANGELOG.md -- https://github.com/unclecode/crawl4ai/blob/main/CHANGELOG.md
- crawl4ai Official Documentation: Deep Crawling -- https://docs.crawl4ai.com/core/deep-crawling/
- crawl4ai Official Documentation: LLM Extraction Strategies -- https://docs.crawl4ai.com/extraction/llm-strategies/
- crawl4ai Official Documentation: Session Management -- https://docs.crawl4ai.com/advanced/session-management/
- MCP Python SDK: FastMCP patterns -- https://github.com/modelcontextprotocol/python-sdk
- MCP Debugging: stdio transport issues -- https://modelcontextprotocol.io/legacy/tools/debugging
- Roo Code GitHub Issue #5462: stdout corruption in stdio MCP servers -- https://github.com/RooCodeInc/Roo-Code/issues/5462
- Anthropic Engineering: Writing Tools for Agents -- https://www.anthropic.com/engineering/writing-tools-for-agents
- crawl4ai cost analysis -- https://memo.d.foundation/breakdown/crawl4ai
- crawl4ai API cost issue #712 -- https://github.com/unclecode/crawl4ai/issues/712

---
*Pitfalls research for: Python MCP Server wrapping crawl4ai for Claude Code*
*Researched: 2026-02-19*
