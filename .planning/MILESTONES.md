# Milestones

## v1.0 MVP (Shipped: 2026-02-22)

**Phases completed:** 8 phases, 18 plans
**Lines of code:** 3,019 (Python + shell)
**Timeline:** 3 days (2026-02-19 → 2026-02-22)
**Git range:** e4fbd41..8348b81 (83 commits)

**Delivered:** A local MCP server wrapping crawl4ai that gives Claude Code full web crawling, structured extraction, multi-page crawling, session management, and self-update capabilities — 12 MCP tools, 28 requirements satisfied.

**Key accomplishments:**
1. MCP server foundation with AsyncWebCrawler singleton, stdio-safe logging, and structured error handling
2. Full single-URL crawl with JS rendering, CSS scoping, custom headers/cookies, cache control, and content filtering
3. Named crawl profiles (fast, js_heavy, stealth) with YAML config and per-call override merging
4. Structured data extraction via both LLM-powered (with cost warning) and CSS-based (free) tools
5. Multi-page crawling — parallel batch, BFS deep crawl, and sitemap crawl with hard limits
6. Session management for authenticated workflows — create, list, destroy named browser sessions
7. Update management — version check tool, startup warning, and offline update script

**MCP Tools (12):** ping, crawl_url, list_profiles, extract_structured, extract_css, crawl_many, deep_crawl, crawl_sitemap, create_session, list_sessions, destroy_session, check_update

---

