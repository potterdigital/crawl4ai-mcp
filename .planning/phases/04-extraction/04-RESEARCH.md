# Phase 4: Extraction - Research

**Researched:** 2026-02-20
**Domain:** crawl4ai extraction strategies (LLM-based and CSS-based structured data extraction)
**Confidence:** HIGH

## Summary

Phase 4 adds two new MCP tools -- `extract_structured` (LLM-powered) and `extract_css` (deterministic CSS selectors) -- that extract structured JSON from web pages. Both tools build on the existing `crawl_url` infrastructure: they use the same `AsyncWebCrawler` singleton, the same `_crawl_with_overrides` helper, and the same profile merge system (`build_run_config`). The key difference is that they set `extraction_strategy` on the `CrawlerRunConfig` and return `result.extracted_content` (a JSON string) instead of markdown.

crawl4ai 0.8.0 (the installed version) provides `LLMExtractionStrategy` and `JsonCssExtractionStrategy` as first-class extraction strategies. Both plug into `CrawlerRunConfig.extraction_strategy` and produce their output on `CrawlResult.extracted_content`. LLM extraction uses litellm under the hood, which auto-resolves API keys from standard environment variables (e.g., `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`). No keys need to be (or should be) passed as tool parameters.

**Primary recommendation:** Add both tools directly in `server.py` following the existing `crawl_url` pattern. The `extract_structured` tool must carry a prominent cost warning in its docstring. Both strategies must have `verbose=False` set to protect the MCP stdio transport. The `extraction_strategy` key must be added to `_PER_CALL_KEYS` in `profiles.py` (or strategies must be set post-construction on the config object).

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| EXTR-01 | LLM extraction with JSON schema + instruction, cost warning | `LLMExtractionStrategy` accepts `schema` (Pydantic `.model_json_schema()` or raw dict), `instruction` (str), `extraction_type="schema"`. Cost warning goes in tool docstring. Token usage available via `strategy.total_usage` (completion_tokens, prompt_tokens, total_tokens). |
| EXTR-02 | CSS/JSON selector extraction, deterministic, no LLM cost | `JsonCssExtractionStrategy` accepts a schema dict with `baseSelector`, `fields` (each with `name`, `selector`, `type`). Supports `text`, `attribute`, `html`, `regex`, `nested`, `nested_list`, `list` field types. No LLM, no API cost. |
| EXTR-03 | LLM extraction requires explicit opt-in, never triggered by crawl_url | `crawl_url` tool sets `extraction_strategy=None` (default). New `extract_structured` tool is a separate entry point. The tools are completely independent. |
| EXTR-04 | LLM API keys from environment variables, never tool parameters | `LLMConfig` auto-resolves via litellm: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, etc. The tool accepts `provider` (str) but NOT `api_token`. Key resolution happens server-side. |
</phase_requirements>

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| crawl4ai | 0.8.0 | `LLMExtractionStrategy`, `JsonCssExtractionStrategy`, `LLMConfig` | Already installed; these are crawl4ai's built-in extraction strategies |
| litellm | (transitive via crawl4ai) | LLM provider routing, env var key resolution | crawl4ai delegates all LLM calls to litellm; supports 100+ providers |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pydantic | (transitive via crawl4ai) | JSON schema generation for LLM extraction | Users generate schemas via `Model.model_json_schema()`, but our tool accepts raw dict |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| crawl4ai's LLMExtractionStrategy | Direct litellm calls on markdown | Would lose chunking, overlap, input_format handling that crawl4ai provides for free |
| crawl4ai's JsonCssExtractionStrategy | BeautifulSoup + manual CSS parsing | Would lose nested/list types, transforms, attribute extraction that the strategy handles |

**Installation:** No new dependencies needed. Everything is already available in crawl4ai 0.8.0.

## Architecture Patterns

### Recommended Project Structure

Both tools live in `server.py` for now (consistent with existing `crawl_url` and `ping`):

```
src/crawl4ai_mcp/
├── server.py           # Add extract_structured + extract_css tools here
├── profiles.py         # Add extraction_strategy to _PER_CALL_KEYS (or skip profile merging for extraction)
├── profiles/           # No changes needed
```

### Pattern 1: Extraction Tool Structure

**What:** Both extraction tools follow the same shape as `crawl_url` -- accept URL + extraction params, build CrawlerRunConfig, call `_crawl_with_overrides`, return `result.extracted_content`.

**When to use:** For all extraction tools.

**Example:**

```python
@mcp.tool()
async def extract_structured(
    url: str,
    # LLM-specific params
    schema: dict,
    instruction: str,
    provider: str = "openai/gpt-4o-mini",
    # Shared crawl params (subset)
    css_selector: str | None = None,
    wait_for: str | None = None,
    js_code: str | None = None,
    page_timeout: int = 60,
    ctx: Context[ServerSession, AppContext] = None,
) -> str:
    """Extract structured JSON from a page using an LLM.

    WARNING: This tool calls an external LLM API and incurs token costs.
    Each call may cost $0.01-$1+ depending on page size and model.
    Use extract_css for cost-free deterministic extraction when possible.
    ...
    """
    app: AppContext = ctx.request_context.lifespan_context

    llm_config = LLMConfig(provider=provider)  # api_token auto-resolved from env
    strategy = LLMExtractionStrategy(
        llm_config=llm_config,
        schema=schema,
        extraction_type="schema",
        instruction=instruction,
        input_format="fit_markdown",
        verbose=False,  # CRITICAL: protect MCP transport
    )

    run_cfg = CrawlerRunConfig(
        extraction_strategy=strategy,
        page_timeout=page_timeout * 1000,
        verbose=False,
    )
    if css_selector:
        run_cfg.css_selector = css_selector
    # ... other optional params

    result = await _crawl_with_overrides(app.crawler, url, run_cfg)

    if not result.success:
        return _format_crawl_error(url, result)

    # Include usage stats in response
    usage = strategy.total_usage
    content = result.extracted_content or "[]"
    return (
        f"{content}\n\n"
        f"--- LLM Usage ---\n"
        f"Provider: {provider}\n"
        f"Prompt tokens: {usage.prompt_tokens}\n"
        f"Completion tokens: {usage.completion_tokens}\n"
        f"Total tokens: {usage.total_tokens}"
    )
```

### Pattern 2: CSS Extraction Tool Structure

**What:** Deterministic extraction using CSS selectors -- no LLM, no API cost.

```python
@mcp.tool()
async def extract_css(
    url: str,
    schema: dict,
    # Shared crawl params
    css_selector: str | None = None,
    wait_for: str | None = None,
    js_code: str | None = None,
    page_timeout: int = 60,
    ctx: Context[ServerSession, AppContext] = None,
) -> str:
    """Extract structured JSON from a page using CSS selectors (no LLM, no cost).
    ...
    """
    app: AppContext = ctx.request_context.lifespan_context

    strategy = JsonCssExtractionStrategy(schema, verbose=False)

    run_cfg = CrawlerRunConfig(
        extraction_strategy=strategy,
        page_timeout=page_timeout * 1000,
        verbose=False,
    )

    result = await _crawl_with_overrides(app.crawler, url, run_cfg)

    if not result.success:
        return _format_crawl_error(url, result)

    return result.extracted_content or "[]"
```

### Pattern 3: Config Construction for Extraction Tools

**What:** Two options for how extraction tools build their CrawlerRunConfig.

**Option A (recommended): Direct CrawlerRunConfig construction.** Extraction tools don't use `build_run_config` because they need to set `extraction_strategy` (which profiles don't support) and don't need the markdown_generator (extraction bypasses markdown output). They construct `CrawlerRunConfig` directly with only the params they need.

**Option B: Use build_run_config + post-set.** Call `build_run_config(pm, profile, **overrides)` then set `cfg.extraction_strategy = strategy`. This gives profile support but includes a markdown_generator that extraction doesn't use. Slightly wasteful but not harmful.

**Recommendation:** Option A. Extraction tools have different semantics -- they don't need word_count_threshold, PruningContentFilter, or markdown_generator. Direct construction is cleaner and avoids confusion.

### Anti-Patterns to Avoid

- **Never pass `api_token` as a tool parameter.** Keys must come from env vars server-side. This is a security requirement (EXTR-04) and prevents key leakage through MCP protocol.
- **Never call `strategy.show_usage()`.** This calls `print()` which corrupts MCP stdout. Access `strategy.total_usage` attributes directly instead.
- **Never set `verbose=True` on any strategy or CrawlerRunConfig.** Same stdout corruption risk.
- **Never let `crawl_url` trigger LLM extraction.** The tools must be completely separate entry points (EXTR-03).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| LLM-powered extraction | Custom litellm + prompt engineering | `LLMExtractionStrategy` | Handles chunking, overlap, input format conversion, token tracking |
| CSS selector extraction | BeautifulSoup parsing | `JsonCssExtractionStrategy` | Handles nested/list types, transforms, attributes, default values |
| LLM provider routing | Custom env var resolution per provider | `LLMConfig` (wraps litellm) | Auto-resolves standard env vars for 100+ providers |
| Token usage tracking | Manual token counting | `strategy.total_usage` | Already tracks per-request and cumulative usage |

**Key insight:** crawl4ai's extraction strategies handle all the complexity (chunking large pages, merging chunk results, nested CSS extraction, provider routing). The MCP tools are thin wrappers that translate tool parameters into strategy objects and return the results.

## Common Pitfalls

### Pitfall 1: show_usage() corrupts MCP transport

**What goes wrong:** Calling `strategy.show_usage()` after LLM extraction prints to stdout, corrupting the MCP stdio JSON-RPC frames.
**Why it happens:** `show_usage()` uses `print()` internally. crawl4ai was designed for CLI usage, not embedded in a stdio server.
**How to avoid:** Never call `show_usage()`. Instead, read `strategy.total_usage.completion_tokens`, `.prompt_tokens`, `.total_tokens` directly and format them into the return string.
**Warning signs:** Claude Code disconnects immediately after an LLM extraction call.

### Pitfall 2: verbose=True on strategies

**What goes wrong:** `LLMExtractionStrategy(verbose=True)` or `JsonCssExtractionStrategy(schema, verbose=True)` causes internal logging/printing to stdout.
**Why it happens:** crawl4ai strategies accept `verbose` in their constructors, defaulting to False for LLM but potentially True if passed.
**How to avoid:** Always pass `verbose=False` explicitly to all strategy constructors. The `build_run_config` enforcement in profiles.py only covers `CrawlerRunConfig.verbose`, not strategy-level verbose.
**Warning signs:** Extra output appearing in MCP frames.

### Pitfall 3: Missing API key gives cryptic error

**What goes wrong:** When the required env var (e.g., `OPENAI_API_KEY`) is not set, litellm raises an `AuthenticationError` with a message about invalid API key rather than a clear "env var not set" message.
**Why it happens:** `LLMConfig` resolves to `None` when the env var is missing, litellm then tries to call the API with a None key.
**How to avoid:** Pre-check that the env var exists before creating the strategy. Map provider prefixes to expected env var names and validate. Return a structured error string like `_format_crawl_error` does.
**Warning signs:** `AuthenticationError` or `APIError` in the tool response.

### Pitfall 4: extraction_strategy in profiles.py key validation

**What goes wrong:** If extraction tools use `build_run_config` and pass `extraction_strategy` as a kwarg, it gets stripped as an "unknown key" because it's not in `_ALL_VALID_KEYS`.
**Why it happens:** `_PER_CALL_KEYS` was designed for crawl_url's simple kwargs, not object-typed params like strategies.
**How to avoid:** Either (a) don't route extraction tools through `build_run_config` (recommended), or (b) add `extraction_strategy` to `_PER_CALL_KEYS` and handle it specially in `build_run_config`.
**Warning signs:** Warning log "Stripping unknown profile keys ['extraction_strategy']" and extraction returning markdown instead of structured data.

### Pitfall 5: extracted_content is None when strategy errors silently

**What goes wrong:** `result.extracted_content` is `None` or an empty string even when `result.success` is `True`, because the extraction strategy failed silently (e.g., no CSS matches, or LLM returned unparseable JSON).
**Why it happens:** crawl4ai considers the crawl successful if the page loaded, even if the extraction strategy produced no output.
**How to avoid:** Check `result.extracted_content` for None/empty after confirming success, and return a meaningful "no data extracted" message to Claude.
**Warning signs:** Tool returns `"[]"` or empty string when data was expected.

### Pitfall 6: LLMExtractionStrategy leaking provider/api_token legacy params

**What goes wrong:** `LLMExtractionStrategy` still accepts legacy `provider` and `api_token` kwargs (in addition to `llm_config`). If both are passed, behavior is confusing.
**Why it happens:** Backward compatibility in crawl4ai 0.8.0 -- the old `provider`/`api_token` params still exist alongside the new `llm_config` param.
**How to avoid:** Always use `llm_config=LLMConfig(provider=...)` exclusively. Never pass `provider` or `api_token` directly to `LLMExtractionStrategy`.
**Warning signs:** Deprecation warnings or unexpected provider selection.

## Code Examples

Verified patterns from crawl4ai 0.8.0 (installed and tested):

### LLM Extraction with Schema

```python
# Source: crawl4ai docs + verified against installed 0.8.0 API
from crawl4ai import LLMExtractionStrategy, LLMConfig, CrawlerRunConfig

strategy = LLMExtractionStrategy(
    llm_config=LLMConfig(provider="openai/gpt-4o-mini"),  # auto-resolves OPENAI_API_KEY
    schema={"type": "object", "properties": {"title": {"type": "string"}, "price": {"type": "string"}}},
    extraction_type="schema",
    instruction="Extract product name and price from the content.",
    input_format="fit_markdown",
    chunk_token_threshold=2048,
    apply_chunking=True,
    verbose=False,
)

cfg = CrawlerRunConfig(extraction_strategy=strategy, verbose=False)
result = await crawler.arun(url="https://example.com", config=cfg)

# result.extracted_content is a JSON string: '[{"title": "...", "price": "..."}]'
# strategy.total_usage.total_tokens gives token count
```

### CSS Extraction with Nested Fields

```python
# Source: crawl4ai docs + verified against installed 0.8.0 API
from crawl4ai import JsonCssExtractionStrategy, CrawlerRunConfig

schema = {
    "name": "Products",
    "baseSelector": "div.product-card",
    "fields": [
        {"name": "title", "selector": "h2.title", "type": "text"},
        {"name": "price", "selector": ".price", "type": "text", "transform": "strip"},
        {"name": "image_url", "selector": "img", "type": "attribute", "attribute": "src"},
        {"name": "features", "selector": "ul.features li", "type": "list",
         "fields": [{"name": "feature", "type": "text"}]},
    ],
}

strategy = JsonCssExtractionStrategy(schema, verbose=False)
cfg = CrawlerRunConfig(extraction_strategy=strategy, verbose=False)
result = await crawler.arun(url="https://example.com", config=cfg)

# result.extracted_content is JSON: [{"title": "...", "price": "...", ...}]
```

### Provider-to-Env-Var Mapping (for pre-validation)

```python
# Derived from crawl4ai 0.8.0 LLMConfig source + litellm conventions
PROVIDER_ENV_VARS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "groq": "GROQ_API_KEY",
    "ollama": None,  # No key needed (local)
}

def _check_api_key(provider: str) -> str | None:
    """Return an error message if the required API key env var is not set."""
    prefix = provider.split("/")[0]
    env_var = PROVIDER_ENV_VARS.get(prefix)
    if env_var is None:
        return None  # ollama or unknown provider -- let litellm handle it
    if not os.getenv(env_var):
        return (
            f"LLM extraction failed\n"
            f"Provider: {provider}\n"
            f"Error: Environment variable {env_var} is not set.\n"
            f"Set it on the MCP server process to use this provider."
        )
    return None
```

### Token Usage Reporting (stdout-safe)

```python
# NEVER call strategy.show_usage() -- it uses print() which corrupts MCP transport
usage = strategy.total_usage
usage_report = (
    f"\n--- LLM Usage ---\n"
    f"Provider: {provider}\n"
    f"Prompt tokens: {usage.prompt_tokens}\n"
    f"Completion tokens: {usage.completion_tokens}\n"
    f"Total tokens: {usage.total_tokens}"
)
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `LLMExtractionStrategy(provider=..., api_token=...)` | `LLMExtractionStrategy(llm_config=LLMConfig(provider=...))` | crawl4ai 0.8.0 | Old params still work but `llm_config` is preferred |
| `CosineStrategy` for similarity-based extraction | Still available but rarely used | crawl4ai 0.6.x | `JsonCssExtractionStrategy` is more predictable for structured data |
| Manual chunking of page content | `apply_chunking=True` with `chunk_token_threshold` | crawl4ai 0.7.x | Built-in chunking handles large pages automatically |

**Deprecated/outdated:**
- Direct `provider`/`api_token` params on `LLMExtractionStrategy`: Still functional in 0.8.0 but `llm_config` is the canonical way. Use `llm_config` exclusively.

## Open Questions

1. **Profile support for extraction tools?**
   - What we know: Extraction tools need `extraction_strategy` which is an object, not a simple key-value. Profiles are key-value YAML.
   - What's unclear: Should extraction tools support the `profile` param for setting page_timeout, wait_for, etc.?
   - Recommendation: For Phase 4, skip profile support. Construct CrawlerRunConfig directly. If needed later, add `extraction_strategy` to `_PER_CALL_KEYS` and handle it in `build_run_config`.

2. **Should extract_structured accept a full Pydantic model_json_schema or a simplified schema?**
   - What we know: `LLMExtractionStrategy.schema` accepts any dict. Pydantic's `.model_json_schema()` produces detailed JSON Schema. A simpler `{"properties": {...}}` also works.
   - What's unclear: Whether Claude will consistently generate valid schemas of either format.
   - Recommendation: Accept raw dict. Document that both Pydantic `.model_json_schema()` output and simple `{"type": "object", "properties": {...}}` formats work.

3. **Should LLM extraction chunk token threshold and overlap be tool parameters?**
   - What we know: `chunk_token_threshold` (default 2048) and `overlap_rate` (default 0.1) control how large pages are split for LLM processing.
   - What's unclear: Whether Claude would benefit from tuning these per-call.
   - Recommendation: Use sensible defaults. Optionally expose `chunk_token_threshold` as a parameter but keep `overlap_rate` internal.

4. **input_format: which default is best for MCP usage?**
   - What we know: Options are `"markdown"` (default), `"fit_markdown"`, `"html"`. `fit_markdown` is pre-filtered, smaller, cheaper. `html` preserves structure but is larger.
   - What's unclear: Which format gives best extraction quality for the token cost.
   - Recommendation: Default to `"fit_markdown"` to minimize token cost. Optionally expose as parameter.

## Sources

### Primary (HIGH confidence)
- `/websites/crawl4ai` Context7 library (benchmark 90.7) - LLMExtractionStrategy, JsonCssExtractionStrategy schema format, CrawlerRunConfig integration
- `/unclecode/crawl4ai` Context7 library (benchmark 85.7) - Hybrid extraction patterns, nested CSS schemas, LLM strategy config
- Installed crawl4ai 0.8.0 source code - `LLMExtractionStrategy.__init__` signature, `LLMConfig.__init__` signature, `JsonCssExtractionStrategy.__init__` signature, `PROVIDER_MODELS_PREFIXES` mapping, `TokenUsage` dataclass (all verified via `inspect` against installed package)
- Existing codebase (via Serena) - `AppContext`, `_crawl_with_overrides`, `_format_crawl_error`, `build_run_config`, `_PER_CALL_KEYS`, `_ALL_VALID_KEYS` patterns

### Secondary (MEDIUM confidence)
- Perplexity search: litellm env var auto-detection confirmed across multiple sources (docs.litellm.ai)
- Perplexity search: crawl4ai LLMConfig env var resolution confirmed (docs.crawl4ai.com)

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - crawl4ai 0.8.0 installed, all imports verified, constructors inspected
- Architecture: HIGH - Existing codebase patterns thoroughly explored via Serena; extraction strategies tested against installed API
- Pitfalls: HIGH - stdout corruption risks identified from source code analysis; key resolution traced through actual LLMConfig source

**Research date:** 2026-02-20
**Valid until:** 2026-03-20 (crawl4ai 0.8.x stable; pinned in pyproject.toml to <0.9.0)
