# Phase 5: Multi-Page Crawl - Context

**Gathered:** 2026-02-21
**Status:** Ready for planning

<domain>
## Phase Boundary

Three tools for crawling beyond single pages: `crawl_many` (parallel batch), `deep_crawl` (BFS link-following to configurable depth), and `crawl_sitemap` (XML sitemap parsing + crawl). Hard limits prevent runaway crawls. This phase does NOT include DFS strategy, link scoring/filtering beyond URL patterns, or sitemap URL filtering (those are v2).

</domain>

<decisions>
## Implementation Decisions

### Design philosophy
- The MCP server should expose **maximum flexibility through parameters** and let the AI agent decide what it wants
- Tool descriptions should make clear that controls exist and are adjustable (page count, detail level, concurrency, scope, etc.)
- The agent — not the server — decides how many pages to return, level of detail, output structure, etc.

### Result format
- Claude's Discretion: output structure, whether to return per-URL blocks vs summary + content, markdown vs JSON, level of detail
- The tools should expose parameters that give the agent control over what comes back

### Safety limits
- Default `max_pages` for `deep_crawl`: **100+** (higher than the originally proposed 50)
- No runtime timeout for multi-page operations — page count limits are sufficient
- No artificial concurrency cap on `crawl_many` — the agent decides parallelism
- All limits should be agent-configurable per call

### Partial failure handling
- **Always return both** successes and failures — never discard successful results because of individual URL errors
- Never fail an entire batch for individual errors
- Claude's Discretion: error detail level, BFS traversal resilience strategy, progress reporting approach

### Deep crawl scope
- Link-following domain scope is **agent-configurable** — expose a parameter for same-domain, same-root-domain, or any domain
- **URL pattern filtering**: include an optional `include_pattern`/`exclude_pattern` param so the agent can filter which links to follow (e.g., "only follow /docs/* links")
- **Always deduplicate** — each unique URL is crawled at most once, regardless of how many pages link to it
- No robots.txt enforcement — compliance is the user's responsibility (consistent with project out-of-scope list)

### Claude's Discretion
- Output structure and format for all three tools
- Sensible hard cap ceiling (if any)
- BFS branch resilience when dead zones are hit
- Progress reporting strategy (intermediate updates vs final results)
- Error detail level in failure reports
- Compression/summarization of large result sets

</decisions>

<specifics>
## Specific Ideas

- "The MCP server should let the agent know it can tell the MCP server what it wants" — tool descriptions should explicitly communicate available controls
- Agent autonomy is the priority: expose knobs, don't make opinionated choices about how the agent uses them
- Higher default limits than typical (100+ pages) since the agent can always request fewer

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 05-multi-page-crawl*
*Context gathered: 2026-02-21*
