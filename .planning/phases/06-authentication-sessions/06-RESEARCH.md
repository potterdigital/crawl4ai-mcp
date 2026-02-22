# Phase 6: Authentication & Sessions - Research

**Researched:** 2026-02-21
**Domain:** crawl4ai session management, Playwright browser contexts, cookie injection
**Confidence:** HIGH

## Summary

crawl4ai 0.8.0 has native, well-documented session management via the `session_id` parameter on `CrawlerRunConfig`. When the same `session_id` is passed across multiple `arun()` calls, the `BrowserManager` reuses the same Playwright page and browser context, preserving cookies, localStorage, and all browser state. Sessions are stored in `BrowserManager.sessions` as `(context, page, last_used_time)` tuples with a 30-minute TTL and auto-cleanup. Sessions are destroyed via `crawler.crawler_strategy.kill_session(session_id)`.

For per-call cookie injection (AUTH-01), the existing `_crawl_with_overrides()` pattern in `server.py` already works -- it uses Playwright hooks (`on_page_context_created`, `before_goto`) to inject cookies and headers per-request, then clears hooks in a `finally` block. This pattern is sound for single-call use but has a concurrency hazard: hooks are set on the shared `crawler_strategy` singleton, so concurrent tool calls could race on hook state. For Phase 6, this is acceptable since MCP stdio transport is inherently sequential (one tool call at a time), but should be documented.

For persistent sessions (AUTH-02/AUTH-03), we use crawl4ai's native `session_id` on `CrawlerRunConfig`. The `AppContext` needs a lightweight session registry to track which session names are active (for `list_sessions` and `destroy_session`), but the actual browser state management is handled entirely by crawl4ai's `BrowserManager`.

**Primary recommendation:** Use crawl4ai's native `session_id` for persistent sessions. Keep the existing hook-based cookie injection for per-call cookies. Add a `SessionManager` helper to `AppContext` that wraps session lifecycle (create, list, destroy) and delegates to `crawler.crawler_strategy.kill_session()` for cleanup.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| AUTH-01 | User can inject cookies into any crawl call (per-call, non-persistent) | Existing `_crawl_with_overrides()` already handles this via Playwright hooks. The `cookies` param on `crawl_url` works today. Need to verify it works with session-based calls too (confirmed: `on_page_context_created` hook fires even when reusing a session page). |
| AUTH-02 | User can create a named session that maintains browser state across multiple crawl calls | crawl4ai 0.8.0 `session_id` on `CrawlerRunConfig` does exactly this. `BrowserManager.get_page()` reuses `(context, page)` when `session_id` matches. 30-minute TTL with auto-cleanup. Need new tools: `create_session` and a `session_id` param on `crawl_url`. |
| AUTH-03 | User can list and destroy active sessions via MCP tools | `BrowserManager.sessions` dict holds all active sessions. `crawler.crawler_strategy.kill_session(session_id)` closes page and context. Need `list_sessions` and `destroy_session` MCP tools that wrap these. |
</phase_requirements>

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| crawl4ai | 0.8.0 | Web crawler with built-in session management | Already installed; `session_id` on `CrawlerRunConfig` is the native session API |
| playwright | (bundled) | Browser automation underlying crawl4ai | Provides `BrowserContext` and `Page` objects that hold session state |

### Supporting

No additional libraries needed. All session management is built into crawl4ai 0.8.0.

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| crawl4ai `session_id` | Managing raw Playwright `BrowserContext` objects directly | Unnecessary complexity; crawl4ai already manages contexts via `BrowserManager`. Direct Playwright management would bypass crawl4ai's stealth, hooks, and cleanup systems. |
| `BrowserConfig.cookies` (global) | Per-call cookie injection via hooks | `BrowserConfig.cookies` applies to ALL contexts at creation time; hooks give per-request control. Hooks are the right choice for AUTH-01 (per-call). |
| `BrowserConfig.storage_state` | `session_id` for persistent sessions | `storage_state` is for restoring state from a serialized dict/file, useful for cross-restart persistence but overkill for within-session auth. `session_id` is simpler and sufficient. |
| `BrowserConfig.use_persistent_context` + `user_data_dir` | `session_id` | Persistent context writes to disk and survives server restarts. Our sessions only need to survive across tool calls within a single server lifetime. `session_id` is the right abstraction. |

## Architecture Patterns

### Recommended Module Structure

No new files needed. Session tools go in `server.py` alongside existing tools. A `SessionManager` helper class can be added to `server.py` or factored into a small `sessions.py` if it grows, but the initial implementation is simple enough for `server.py`.

```
src/crawl4ai_mcp/
├── server.py           # + create_session, list_sessions, destroy_session tools
│                       # + session_id param on crawl_url
│                       # + SessionManager helper on AppContext
└── (no new files)
```

### Pattern 1: crawl4ai Native Session ID

**What:** Pass `session_id` on `CrawlerRunConfig` to reuse the same Playwright page/context across `arun()` calls.

**When to use:** Multi-step authenticated workflows (login, then crawl protected pages).

**How it works internally:**
1. First `arun()` with `session_id="foo"`: `BrowserManager.get_page()` creates a new page/context and stores it in `self.sessions["foo"] = (context, page, time.time())`
2. Subsequent `arun()` with `session_id="foo"`: `get_page()` finds the existing entry, updates the timestamp, returns the same `(page, context)`
3. All cookies, localStorage, and DOM state persist because it is literally the same Playwright page
4. `kill_session("foo")` closes the page, optionally closes the context, and removes the entry

**Example:**
```python
# Source: crawl4ai docs - https://docs.crawl4ai.com/advanced/session-management
from crawl4ai import CrawlerRunConfig, CacheMode

# Step 1: Login
login_config = CrawlerRunConfig(
    session_id="my_session",
    js_code="document.querySelector('#user').value='admin'; document.querySelector('#pass').value='secret'; document.querySelector('#login').click();",
    wait_for="css:.dashboard",
    cache_mode=CacheMode.BYPASS,
)
result1 = await crawler.arun("https://example.com/login", config=login_config)

# Step 2: Crawl protected page (same session = same cookies)
nav_config = CrawlerRunConfig(
    session_id="my_session",
    cache_mode=CacheMode.BYPASS,
)
result2 = await crawler.arun("https://example.com/dashboard", config=nav_config)

# Step 3: Cleanup
await crawler.crawler_strategy.kill_session("my_session")
```

### Pattern 2: Per-Call Cookie Injection via Hooks

**What:** Inject cookies into a single crawl request without persisting them, using Playwright context hooks.

**When to use:** One-off authenticated crawl where you already have the cookie values (e.g., from a browser extension export).

**How it works:** The existing `_crawl_with_overrides()` sets `on_page_context_created` hook to call `context.add_cookies(cookies)`, then clears the hook in a `finally` block.

**Example (already implemented in server.py):**
```python
cookies = [{"name": "session", "value": "abc123", "domain": "example.com"}]
result = await _crawl_with_overrides(crawler, url, run_cfg, cookies=cookies)
```

### Pattern 3: SessionManager on AppContext

**What:** A lightweight wrapper that tracks active session names and delegates to crawl4ai for actual session lifecycle.

**Why needed:** crawl4ai's `BrowserManager.sessions` is internal and not exposed through a clean public API. We need to know which sessions exist for `list_sessions` and which to clean up on server shutdown.

**Example:**
```python
@dataclass
class AppContext:
    crawler: AsyncWebCrawler
    profile_manager: ProfileManager
    sessions: dict[str, float]  # session_id -> creation_time

    def register_session(self, session_id: str) -> None:
        self.sessions[session_id] = time.time()

    def active_sessions(self) -> list[str]:
        return sorted(self.sessions.keys())

    async def destroy_session(self, session_id: str) -> bool:
        if session_id not in self.sessions:
            return False
        await self.crawler.crawler_strategy.kill_session(session_id)
        del self.sessions[session_id]
        return True
```

### Anti-Patterns to Avoid

- **Creating a new AsyncWebCrawler per session:** Never. The singleton must be reused. Sessions are Playwright pages within the same browser, not separate browser instances.
- **Setting BrowserConfig.cookies for per-call injection:** This applies cookies globally to all new browser contexts, not per-request. Use hooks instead.
- **Forgetting to clean up sessions on server shutdown:** If the server exits without killing sessions, Playwright pages remain open until the browser process terminates (which it will, since `crawler.close()` is in `app_lifespan`'s `finally` block). But explicit cleanup is cleaner and prevents memory accumulation during long server runs.
- **Using js_only=True without session_id:** `js_only=True` skips page navigation and only runs JavaScript. It only makes sense when reusing an existing session page. Without `session_id`, there is no page to run JS on.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Persistent browser state across calls | Custom Playwright BrowserContext management | crawl4ai `session_id` on `CrawlerRunConfig` | crawl4ai's `BrowserManager` already handles context creation, page reuse, TTL expiry, and cleanup. Reimplementing this would duplicate 200+ lines of tested code and miss edge cases (lock management, stealth scripts, CDP mode). |
| Per-call cookie injection | Direct Playwright `context.add_cookies()` calls | Existing `_crawl_with_overrides()` hook pattern | The hook pattern correctly handles cleanup in `finally`, preventing cookie leakage to subsequent calls. |
| Session TTL and auto-cleanup | Custom timer/scheduler | crawl4ai's `_cleanup_expired_sessions()` | Already runs on every `get_page()` call, cleaning up sessions older than 30 minutes. |

## Common Pitfalls

### Pitfall 1: Hook Concurrency on Shared Strategy

**What goes wrong:** The `_crawl_with_overrides()` function sets hooks on the shared `crawler_strategy` singleton. If two tool calls ran concurrently, one could overwrite the other's hooks.

**Why it happens:** Hooks are instance-level state on `AsyncPlaywrightCrawlerStrategy`, not per-request.

**How to avoid:** MCP stdio transport processes one tool call at a time (synchronous request/response), so this is not a problem in practice. Document this assumption. If future transport modes enable concurrency, hooks would need to be wrapped in an asyncio.Lock or replaced with per-config cookie injection.

**Warning signs:** If you ever see `cookies from a different request appearing in a crawl`, this is the cause.

### Pitfall 2: Session ID Collision

**What goes wrong:** Two different workflows accidentally use the same `session_id` string, causing them to share browser state unexpectedly.

**Why it happens:** Session IDs are user-provided strings with no namespace isolation.

**How to avoid:** Document that session IDs should be unique per workflow. The `create_session` tool could auto-generate a UUID-based session ID and return it, removing the burden from the user.

**Warning signs:** Unexpected cookies or login state appearing in a session that should be fresh.

### Pitfall 3: Session TTL Expiry Mid-Workflow

**What goes wrong:** A session expires (30-minute TTL) between tool calls because the user took too long.

**Why it happens:** `BrowserManager._cleanup_expired_sessions()` runs on every `get_page()` call and deletes sessions where `current_time - last_used > 1800`.

**How to avoid:** The TTL is reset on every `get_page()` call (timestamp is updated). So as long as the user makes at least one crawl call per 30 minutes, the session stays alive. Document this TTL. Consider whether to surface it as a parameter or just document the default.

**Warning signs:** A `session_id` that was working suddenly creates a fresh page with no state.

### Pitfall 4: Forgetting verbose=False on Session Configs

**What goes wrong:** `CrawlerRunConfig(verbose=True)` causes Rich Console output to stdout, corrupting the MCP transport.

**Why it happens:** `CrawlerRunConfig` defaults to `verbose=True`.

**How to avoid:** All CrawlerRunConfig instances MUST go through `build_run_config()` which forces `verbose=False`. Session-related configs must use the same pattern, never construct `CrawlerRunConfig` directly without setting `verbose=False`.

### Pitfall 5: on_page_context_created Hook Fires on Session Reuse

**What goes wrong (or right):** The `on_page_context_created` hook fires on EVERY `arun()` call, even when reusing a session. This means per-call cookie injection via `_crawl_with_overrides()` will add cookies even on session-scoped calls.

**Why it matters:** This is actually desirable behavior -- it means cookies param works alongside session_id. But it could surprise someone who expects hooks to only fire on new pages.

**How to handle:** Document this behavior. It means `crawl_url(url, session_id="foo", cookies=[...])` will both reuse the session AND inject additional cookies, which is a useful combination.

## Code Examples

### Creating a Session Tool

```python
# Source: Derived from crawl4ai session management docs + existing codebase patterns
@mcp.tool()
async def create_session(
    session_id: str | None = None,
    url: str | None = None,
    cookies: list | None = None,
    headers: dict | None = None,
    ctx: Context[ServerSession, AppContext] = None,
) -> str:
    """Create a named browser session for multi-step authenticated workflows.

    The session maintains cookies, localStorage, and browser state across
    multiple crawl_url calls that reference the same session_id.
    """
    app: AppContext = ctx.request_context.lifespan_context
    sid = session_id or str(uuid.uuid4())

    # Create session by doing an initial crawl (or just creating the page)
    config = CrawlerRunConfig(
        session_id=sid,
        cache_mode=CacheMode.BYPASS,
        verbose=False,  # CRITICAL
    )

    if url:
        result = await _crawl_with_overrides(app.crawler, url, config, headers, cookies)
        # ... handle result ...
    else:
        # Just create the session page without navigating
        await app.crawler.crawler_strategy.create_session(session_id=sid)

    app.sessions[sid] = time.time()
    return f"Session created: {sid}"
```

### Adding session_id to crawl_url

```python
# Add to crawl_url signature:
async def crawl_url(
    url: str,
    session_id: str | None = None,  # NEW: reuse named session
    # ... existing params ...
) -> str:
    # In per_call_kwargs building:
    if session_id is not None:
        per_call_kwargs["session_id"] = session_id
    # Everything else stays the same -- build_run_config handles the merge
```

### Listing Active Sessions

```python
@mcp.tool()
async def list_sessions(ctx: Context[ServerSession, AppContext]) -> str:
    """List all active named browser sessions."""
    app: AppContext = ctx.request_context.lifespan_context
    if not app.sessions:
        return "No active sessions."
    lines = ["Active sessions:"]
    for sid, created in sorted(app.sessions.items()):
        age_mins = (time.time() - created) / 60
        lines.append(f"  - {sid} (created {age_mins:.0f} min ago)")
    return "\n".join(lines)
```

### Destroying a Session

```python
@mcp.tool()
async def destroy_session(
    session_id: str,
    ctx: Context[ServerSession, AppContext] = None,
) -> str:
    """Destroy a named browser session and free its resources."""
    app: AppContext = ctx.request_context.lifespan_context
    if session_id not in app.sessions:
        return f"Session not found: {session_id}"
    await app.crawler.crawler_strategy.kill_session(session_id)
    del app.sessions[session_id]
    return f"Session destroyed: {session_id}"
```

### Session Cleanup on Server Shutdown

```python
@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    # ... existing setup ...
    try:
        yield AppContext(crawler=crawler, profile_manager=profile_manager, sessions={})
    finally:
        # Clean up any active sessions before closing browser
        for sid in list(app_ctx.sessions.keys()):
            try:
                await crawler.crawler_strategy.kill_session(sid)
            except Exception:
                pass
        await crawler.close()
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `crawler.crawler_strategy.kill_session()` (manual) | Auto-kill with TTL (30 min) + manual kill still available | crawl4ai 0.8.0 | `kill_session` now logs a warning that auto-kill is enabled, but still works. Both approaches coexist. |
| Hooks on `crawler_strategy` for cookie injection | BrowserConfig has `cookies` and `headers` params | crawl4ai 0.8.0 | BrowserConfig-level cookies/headers are global. Hooks remain the only way to do per-request injection. |
| `bypass_cache=True` (old API) | `CacheMode.BYPASS` enum | crawl4ai 0.7.x+ | Old boolean params still accepted but deprecated. |

**Important note on `session_id` in `CrawlerRunConfig`:** The `session_id` field has been present since at least crawl4ai 0.3.x and is well-documented in 0.8.0. It is the canonical way to manage persistent browser state.

## Open Questions

1. **Should `create_session` navigate to a URL or just create the page?**
   - What we know: `crawler_strategy.create_session()` creates a page without navigating. Alternatively, calling `arun()` with a `session_id` on a URL creates the session AND navigates.
   - What's unclear: Which UX is better for Claude -- a separate creation step, or just allowing `crawl_url(session_id="x")` to implicitly create the session on first use?
   - Recommendation: Support both. `crawl_url` with a `session_id` implicitly creates the session if it does not exist (crawl4ai does this natively). `create_session` is an explicit tool for when you want to set up a session with initial cookies/headers before crawling. `create_session` with a `url` param navigates to it; without `url`, it just creates the browser page.

2. **Should session_id be exposed on multi-page tools (crawl_many, deep_crawl)?**
   - What we know: Multi-page tools create multiple pages concurrently. A session_id reuses ONE page.
   - Recommendation: No. Session support only on `crawl_url` (and extraction tools if needed). Multi-page tools are inherently parallel and don't fit the serial session model.

3. **Session TTL: should it be configurable?**
   - What we know: Hardcoded at 30 minutes in `BrowserManager`. Not exposed as a parameter.
   - Recommendation: Document the 30-minute TTL. Don't try to override it -- the value is reasonable for interactive use. If it becomes a problem, address in v2.

## Sources

### Primary (HIGH confidence)
- crawl4ai 0.8.0 source code (direct inspection via `inspect.getsource()`) -- `BrowserManager.get_page()`, `BrowserManager.kill_session()`, `AsyncPlaywrightCrawlerStrategy.create_session()`, `AsyncPlaywrightCrawlerStrategy.kill_session()`, `AsyncPlaywrightCrawlerStrategy.set_hook()`
- Context7 `/websites/crawl4ai` -- session management docs, identity-based crawling, storage_state usage
- crawl4ai official docs: https://docs.crawl4ai.com/advanced/session-management
- crawl4ai official docs: https://docs.crawl4ai.com/advanced/identity-based-crawling

### Secondary (MEDIUM confidence)
- Perplexity search on crawl4ai session management (verified against source code)

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- crawl4ai 0.8.0 source code directly inspected, session_id behavior confirmed by reading `BrowserManager.get_page()` and `kill_session()`
- Architecture: HIGH -- pattern derived from existing codebase (`_crawl_with_overrides`, `AppContext`, `build_run_config`) and verified crawl4ai APIs
- Pitfalls: HIGH -- concurrency hazard confirmed by reading hook implementation; TTL behavior confirmed from `_cleanup_expired_sessions()` source; verbose=False requirement is established project constraint

**Research date:** 2026-02-21
**Valid until:** 2026-03-21 (30 days -- crawl4ai 0.8.0 API is stable)
