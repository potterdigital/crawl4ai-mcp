# Phase 3: Profile System - Research

**Researched:** 2026-02-19
**Domain:** crawl4ai BrowserConfig/CrawlerRunConfig profile mapping, YAML config loading, Python dict merge strategy, ProfileManager design
**Confidence:** HIGH (core findings verified against Context7 crawl4ai official docs and installed 0.8.x source; merge strategy verified against Python docs)

## Summary

Phase 3 adds named YAML profiles (`fast`, `js-heavy`, `stealth`) and a `list_profiles` tool. The central architectural discovery is that crawl4ai splits config across two classes with different lifecycles: `BrowserConfig` is browser-wide (singleton), while `CrawlerRunConfig` is per-`arun()` call. This split directly affects what profile settings can be applied dynamically.

The good news: **all three required profiles can be implemented entirely via `CrawlerRunConfig`** — the per-call config. Stealth's most impactful settings (`magic=True`, `simulate_user=True`, `override_navigator=True`, `delay_before_return_html`) live in `CrawlerRunConfig`. The BrowserConfig-level stealth settings (`enable_stealth`, `user_agent_mode="random"`) would require a browser restart to apply, which is incompatible with the MCP singleton architecture. This is an acceptable tradeoff — the `CrawlerRunConfig` stealth settings are sufficient for the stated requirement.

The merge strategy is straightforward: profiles are stored as plain dicts loaded from YAML. Merge order `default → profile → per-call overrides` uses Python's `{**base, **profile, **overrides}` shallow merge, which is correct because all profile keys are scalar (no nested dicts requiring deep merge in this use case). The `CrawlerRunConfig.clone(**kwargs)` method is the natural fit for applying overrides on top of a base config. `_build_run_config()` in `server.py` must be refactored to become the single point where profile kwargs are merged with per-call overrides before constructing `CrawlerRunConfig`.

**Primary recommendation:** Implement profiles as flat YAML dicts mapped to `CrawlerRunConfig` kwargs only. Load all profiles at startup via `ProfileManager`. Add a `profile` parameter to `crawl_url`. Refactor `_build_run_config()` into a `build_run_config_from_profile(profile_name, **overrides)` function that handles the merge. Add `list_profiles` as a new MCP tool.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| PROF-01 | Server ships with three built-in profiles: `fast`, `js-heavy`, `stealth` | Verified field sets for each profile; all map to `CrawlerRunConfig` kwargs. See Code Examples section. |
| PROF-02 | `list_profiles` MCP tool returns all available profiles with full configuration | `ProfileManager.all()` returns dict of name → config dict; serialize to YAML or formatted string for Claude. |
| PROF-03 | User adds/edits custom profiles via YAML files in `profiles/` dir without code changes | `ProfileManager` scans `profiles/*.yaml` at startup; adding a file auto-registers a new profile. |
| PROF-04 | Any `crawl_url` call can specify `profile` name; per-call params override profile values (default → profile → per-call) | `{**default_profile, **named_profile, **per_call_overrides}` shallow merge, then pass to `CrawlerRunConfig`. `CrawlerRunConfig.clone()` is the natural application mechanism. |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `PyYAML` | bundled with crawl4ai deps | Load `profiles/*.yaml` into Python dicts | Standard; already transitively available. Use `yaml.safe_load()` — never `yaml.load()`. |
| `crawl4ai.CrawlerRunConfig` | 0.8.x | Target of profile field mapping | All required profile fields are native `CrawlerRunConfig` kwargs — no adapter needed |
| `crawl4ai.CrawlerRunConfig.clone()` | 0.8.x | Apply per-call overrides onto profile-built config | Built-in — does `to_dict() → update(kwargs) → from_kwargs()` internally |
| `pathlib.Path` | stdlib | Scan `profiles/*.yaml` at startup | Standard pattern for config file discovery |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `importlib.resources` | stdlib | Locate bundled `profiles/` dir relative to package | If profiles are shipped inside the Python package (src layout) |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| PyYAML `safe_load` | `ruamel.yaml` | ruamel preserves comments and round-trips; PyYAML is simpler. Since we only read (never write back), PyYAML is sufficient. |
| Flat profile dicts | Pydantic schema validation | Pydantic would provide type-checking per field but adds dependency and complexity. Simple allowlist check (compare keys to `CrawlerRunConfig.from_kwargs()` known keys) is sufficient. |
| `CrawlerRunConfig.clone()` for merge | Manual dict merge + `CrawlerRunConfig(**merged)` | Both work. `clone()` ensures consistent construction path; manual merge is equally valid since `from_kwargs` handles unknowns gracefully (silently ignores them via `.get()`). |

**Installation:** No new dependencies needed. PyYAML is transitively available via crawl4ai. Verify with `uv run python -c "import yaml; print(yaml.__version__)"`.

## Architecture Patterns

### Recommended Project Structure

```
src/crawl4ai_mcp/
├── server.py           # FastMCP instance, lifespan, tool imports — minimal changes
├── profiles.py         # NEW: ProfileManager class
└── profiles/           # NEW: YAML profile files (shipped with package)
    ├── default.yaml    # Base defaults applied to all crawls
    ├── fast.yaml
    ├── js_heavy.yaml
    └── stealth.yaml
```

No new `tools/` submodule split yet — keep `crawl_url` and `list_profiles` in `server.py` for now unless the file grows unwieldy.

### Pattern 1: ProfileManager — Load and Merge

**What:** A simple class that loads all `*.yaml` files from the `profiles/` directory at startup. Exposes `get(name)` returning a plain dict of CrawlerRunConfig kwargs, and `all()` returning the full registry for `list_profiles`.

**When to use:** Called once in `app_lifespan`; stored in `AppContext`.

```python
# Source: design based on Context7 crawl4ai docs + Python stdlib patterns
import yaml
from pathlib import Path

PROFILES_DIR = Path(__file__).parent / "profiles"
KNOWN_KEYS = {
    # CrawlerRunConfig kwargs (subset relevant to profiles)
    "wait_until", "page_timeout", "delay_before_return_html",
    "simulate_user", "override_navigator", "magic",
    "scan_full_page", "scroll_delay", "remove_overlay_elements",
    "word_count_threshold", "cache_mode",
    "mean_delay", "max_range",
}

class ProfileManager:
    def __init__(self, profiles_dir: Path = PROFILES_DIR):
        self._profiles: dict[str, dict] = {}
        self._load_all(profiles_dir)

    def _load_all(self, profiles_dir: Path) -> None:
        if not profiles_dir.exists():
            logger.warning("profiles/ directory not found at %s", profiles_dir)
            return
        for path in sorted(profiles_dir.glob("*.yaml")):
            name = path.stem  # "fast.yaml" → "fast"
            try:
                data = yaml.safe_load(path.read_text())
                if not isinstance(data, dict):
                    logger.error("Profile %s is not a YAML dict — skipped", name)
                    continue
                unknown = set(data.keys()) - KNOWN_KEYS
                if unknown:
                    logger.warning("Profile %s has unknown keys %s — they will be ignored", name, unknown)
                self._profiles[name] = data
                logger.info("Loaded profile: %s", name)
            except Exception as e:
                logger.error("Failed to load profile %s: %s — skipped", name, e)

    def get(self, name: str) -> dict:
        """Return profile dict, or empty dict if name is None/unknown."""
        return dict(self._profiles.get(name or "", {}))

    def all(self) -> dict[str, dict]:
        """Return all loaded profiles for list_profiles tool."""
        return dict(self._profiles)

    @property
    def names(self) -> list[str]:
        return sorted(self._profiles.keys())
```

### Pattern 2: Merge Order — default → profile → per-call

**What:** Three-layer dict merge. All dicts are flat (no nested dicts in profiles). Python's `{**a, **b, **c}` is the correct approach — later dicts win.

**When to use:** In the refactored `_build_run_config()` (or its replacement) before constructing `CrawlerRunConfig`.

```python
# Source: Python stdlib dict merge, verified pattern
def build_run_config(
    profile_manager: ProfileManager,
    profile: str | None,
    **per_call_overrides,
) -> CrawlerRunConfig:
    default = profile_manager.get("default")      # always-applied base
    named  = profile_manager.get(profile) if profile else {}

    # Merge: default ← profile ← per-call (right wins)
    merged = {**default, **named, **per_call_overrides}

    # CRITICAL: always force verbose=False regardless of profile content
    merged["verbose"] = False

    # Build markdown generator separately (not a YAML-serializable field)
    merged["markdown_generator"] = _make_markdown_generator(
        word_count_threshold=merged.pop("word_count_threshold", 10)
    )

    return CrawlerRunConfig(**merged)
```

**Critical detail:** `verbose=False` must be forced after merge — a profile must never be able to set `verbose=True` since that corrupts the MCP transport.

### Pattern 3: YAML Schema — flat kwargs

Profile YAML files are flat key-value pairs that map directly to `CrawlerRunConfig` constructor kwargs. No nesting, no complex types — only scalars and booleans.

```yaml
# fast.yaml
wait_until: domcontentloaded
page_timeout: 15000        # 15 seconds
word_count_threshold: 5
# No JS waiting, no scroll, no simulate_user
```

```yaml
# stealth.yaml
simulate_user: true
override_navigator: true
magic: true
delay_before_return_html: 2.0
mean_delay: 1.5
max_range: 2.0
wait_until: networkidle
page_timeout: 90000
```

```yaml
# js_heavy.yaml
wait_until: networkidle
page_timeout: 90000
scan_full_page: true
scroll_delay: 0.5
delay_before_return_html: 1.0
remove_overlay_elements: true
```

```yaml
# default.yaml  (applied to every crawl as base)
wait_until: domcontentloaded
page_timeout: 60000
word_count_threshold: 10
```

### Pattern 4: AppContext Extension

Add `profile_manager` to `AppContext` dataclass; initialize in `app_lifespan`.

```python
@dataclass
class AppContext:
    crawler: AsyncWebCrawler
    profile_manager: ProfileManager  # NEW
```

### Pattern 5: list_profiles Tool

```python
@mcp.tool()
async def list_profiles(ctx: Context[ServerSession, AppContext]) -> str:
    """List all available crawl profiles and their configuration."""
    app: AppContext = ctx.request_context.lifespan_context
    profiles = app.profile_manager.all()
    if not profiles:
        return "No profiles loaded."
    lines = []
    for name, cfg in sorted(profiles.items()):
        lines.append(f"## {name}")
        for k, v in sorted(cfg.items()):
            lines.append(f"  {k}: {v}")
    return "\n".join(lines)
```

### Anti-Patterns to Avoid

- **BrowserConfig fields in profiles:** Do not put `enable_stealth`, `user_agent_mode`, `text_mode`, `headers`, or `light_mode` in YAML profiles. These live in `BrowserConfig` which is a singleton initialized at server startup. Changing them per-call would require a browser restart — incompatible with the MCP singleton architecture. The CrawlerRunConfig-level stealth settings (`magic`, `simulate_user`, `override_navigator`) provide adequate stealth without browser restart.

- **Setting `verbose` in any profile YAML:** The `verbose=False` override in the build function guards against this, but KNOWN_KEYS should not include `verbose` at all so unknown-key warnings fire if someone tries.

- **Raising exceptions on bad YAML at startup:** The ProfileManager must log errors and skip bad profiles rather than crash the MCP server. A bad user YAML should never prevent server startup. Built-in profiles (fast, js_heavy, stealth) are code-controlled so they will always be valid.

- **Deep merge:** Profiles are intentionally flat (no nested dicts). Do not introduce nested YAML structures — they complicate merge semantics and offer no benefit for scalar crawl4ai kwargs.

- **Storing `CrawlerRunConfig` objects in ProfileManager:** Store plain dicts. Constructing `CrawlerRunConfig` at load time prevents per-call override merging.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Config cloning with overrides | Custom deep-copy logic | `CrawlerRunConfig.clone(**kwargs)` | Built-in, tested, handles all internal fields |
| YAML loading | Custom parser | `yaml.safe_load()` | Standard; safe from code injection; already available |
| Profile file discovery | Manual file list | `Path.glob("*.yaml")` | stdlib; auto-discovers new user profiles (PROF-03) |

**Key insight:** The crawl4ai config classes already have `clone()`, `from_kwargs()`, and `to_dict()` — use them rather than building custom serialization.

## Common Pitfalls

### Pitfall 1: BrowserConfig vs CrawlerRunConfig Scope Confusion

**What goes wrong:** Stealth profile includes `enable_stealth: true` or `user_agent_mode: random` in YAML. These are `BrowserConfig` fields. Passing them to `CrawlerRunConfig(**merged)` either silently ignores them (if not in `from_kwargs`) or raises `TypeError`.

**Why it happens:** crawl4ai's anti-bot features are split: browser-level identity (`enable_stealth`, `user_agent_mode`, global `headers`) is in `BrowserConfig`; per-request behavior (`magic`, `simulate_user`, `override_navigator`) is in `CrawlerRunConfig`.

**How to avoid:** KNOWN_KEYS allowlist contains only `CrawlerRunConfig` kwargs. Unknown keys trigger a warning and are stripped before passing to `CrawlerRunConfig`. Document in profile YAML comments that browser-level settings are not supported.

**Warning signs:** `TypeError: __init__() got an unexpected keyword argument` when building `CrawlerRunConfig`.

### Pitfall 2: verbose=True Leaking from Profile

**What goes wrong:** A profile YAML contains `verbose: true` (perhaps a user experimenting). The merge produces a `CrawlerRunConfig(verbose=True)` which writes Rich Console output to stdout, corrupting MCP transport.

**Why it happens:** `CrawlerRunConfig` defaults to `verbose=True` internally; a profile explicitly setting it to true would pass through the merge.

**How to avoid:** Force `merged["verbose"] = False` unconditionally after merge, before passing to `CrawlerRunConfig`. Exclude `verbose` from KNOWN_KEYS so unknown-key warnings fire on any profile that tries to set it.

### Pitfall 3: ProfileManager Startup Failure Crashes Server

**What goes wrong:** A user's malformed YAML file (syntax error, wrong type) raises an exception in `ProfileManager.__init__`, which propagates out of `app_lifespan`, killing the MCP server before it can respond to any tool calls.

**Why it happens:** If `_load_all` lets exceptions propagate.

**How to avoid:** Wrap each file load in `try/except Exception`. Log the error to stderr. Skip the bad profile. The server continues with the remaining valid profiles.

### Pitfall 4: Profile Name Collision with "default"

**What goes wrong:** A user creates `profiles/default.yaml` intending to override the built-in defaults, but the system treats it as the base layer — potentially breaking all other profiles that rely on the default.

**Why it happens:** `default.yaml` serves a special role as the always-applied base config.

**How to avoid:** This is actually the intended behavior — `default.yaml` is the base layer. Document it clearly. The `list_profiles` output should distinguish `default` with a note.

### Pitfall 5: page_timeout Unit Mismatch

**What goes wrong:** Profile YAML has `page_timeout: 15` (thinking "seconds") but `CrawlerRunConfig` expects milliseconds. Result: 15ms timeout, every crawl fails.

**Why it happens:** `crawl_url` currently converts seconds to ms with `page_timeout * 1000`. Profiles bypass that conversion.

**How to avoid:** **Profiles use milliseconds directly** (matching `CrawlerRunConfig` native units). Document this clearly in built-in YAML files with comments. The `crawl_url` tool's `page_timeout` parameter remains in seconds (user-facing), but the conversion happens before merge — the per-call override `page_timeout` must also be converted to ms before being passed into the merge dict.

## Code Examples

### Complete CrawlerRunConfig Relevant Fields (verified from Context7 source)

```python
# Source: Context7 /unclecode/crawl4ai — from_kwargs() method, confirmed against docs/md_v2/api/parameters.md

# CrawlerRunConfig fields relevant to profiles (subset):
CrawlerRunConfig(
    # Timing / navigation
    wait_until="domcontentloaded",    # "networkidle" | "domcontentloaded" | "load"
    page_timeout=60000,               # ms; default 60000
    delay_before_return_html=0.1,     # seconds; stealth uses 2.0
    mean_delay=0.1,                   # stealth human-delay mean (seconds)
    max_range=0.3,                    # stealth human-delay max variance

    # Stealth / anti-bot (CrawlerRunConfig only — BrowserConfig owns the rest)
    simulate_user=False,              # random mouse/delay simulation
    override_navigator=False,         # override navigator properties
    magic=False,                      # auto-handle overlays, popups, anti-bot

    # JS interaction
    scan_full_page=False,             # auto-scroll entire page
    scroll_delay=0.2,                 # seconds between auto-scroll steps
    remove_overlay_elements=False,    # remove cookie banners, modals

    # Content
    word_count_threshold=200,         # min words per block (CrawlerRunConfig default is 200!)

    verbose=False,                    # CRITICAL: always False
)
```

**Important:** `CrawlerRunConfig` default `word_count_threshold` is 200 (from `from_kwargs` source). The current `_build_run_config()` uses 10 as its default. The `default.yaml` profile should explicitly set `word_count_threshold: 10` to preserve current behavior.

### BrowserConfig Fields NOT Available in Profiles (require browser restart)

```python
# Source: Context7 /unclecode/crawl4ai — BrowserConfig docs
# These CANNOT be in profile YAML — singleton BrowserConfig set at startup only:
BrowserConfig(
    enable_stealth=False,             # playwright-stealth; requires browser restart
    user_agent_mode="random",         # randomize UA; requires browser restart
    text_mode=False,                  # disable images; requires browser restart
    light_mode=False,                 # reduce background features; requires browser restart
    headers={},                       # global headers; requires browser restart
)
```

### Verified Stealth Settings (CrawlerRunConfig only — safe for profiles)

```python
# Source: Context7 /unclecode/crawl4ai — advanced/undetected-browser.md, identity-based-crawling.md
stealth_run_config = CrawlerRunConfig(
    cache_mode=CacheMode.BYPASS,
    simulate_user=True,
    override_navigator=True,
    magic=True,
    delay_before_return_html=2.0,
    wait_until="networkidle",
    page_timeout=90000,
    verbose=False,
)
```

### Merge Implementation (verified Python pattern)

```python
# Source: Python stdlib — dict union operator (Python 3.9+; project requires 3.12)
default_dict = profile_manager.get("default")   # e.g. {"page_timeout": 60000, ...}
profile_dict = profile_manager.get("fast")       # e.g. {"page_timeout": 15000, ...}
per_call     = {"css_selector": "article"}       # from tool call arguments

merged = {**default_dict, **profile_dict, **per_call}
merged["verbose"] = False  # non-negotiable override

# Then apply markdown generator (not serializable to YAML):
merged["markdown_generator"] = DefaultMarkdownGenerator(
    content_filter=PruningContentFilter(
        threshold=0.48,
        threshold_type="fixed",
        min_word_threshold=merged.pop("word_count_threshold", 10),
    )
)

run_cfg = CrawlerRunConfig(**merged)
```

## State of the Art

| Old Approach | Current Approach | Impact |
|--------------|------------------|--------|
| Build `CrawlerRunConfig` directly in `_build_run_config()` with hardcoded defaults | Load named profiles from YAML; merge default → profile → per-call | Profiles are user-extensible without code changes (PROF-03) |
| Stealth requires external library (undetected-chromedriver) | crawl4ai 0.8.x has native `magic=True`, `simulate_user`, `override_navigator` | No extra deps; built-in and maintained |
| `proxy` param on BrowserConfig | `proxy_config` (dict/ProxyConfig object) | `proxy` is deprecated in 0.8.x; use `proxy_config` |

**Deprecated/outdated:**
- `BrowserConfig(proxy=...)` — deprecated; use `proxy_config={"server": ...}` instead
- `CrawlerRunConfig(bypass_cache=True)` — use `cache_mode=CacheMode.BYPASS` instead

## Open Questions

1. **`word_count_threshold` unit inconsistency**
   - What we know: `CrawlerRunConfig` default is 200; current `_build_run_config()` default is 10; `default.yaml` should set 10 to preserve behavior.
   - What's unclear: Should `word_count_threshold` even be in profiles, or is it better left as a per-call param only?
   - Recommendation: Include it in `default.yaml` and KNOWN_KEYS. Power users can tune it per-profile.

2. **`page_timeout` units in YAML vs tool parameter**
   - What we know: `CrawlerRunConfig` takes ms; `crawl_url` tool takes seconds (multiplied internally).
   - What's unclear: Profiles would use ms natively. This is a potential user confusion point.
   - Recommendation: Document in YAML comments (`# milliseconds`) and in PROF-02 `list_profiles` output. The per-call `page_timeout` parameter (seconds) is converted before being merged into the dict, so there is no collision.

3. **Profile name validation**
   - What we know: `profiles/` dir auto-registers any `*.yaml` filename as a profile name.
   - What's unclear: Should the tool validate `profile=` argument names and return a clear error if the name is not found?
   - Recommendation: Yes — if `profile` is specified and not found, return a structured error string (matching `_format_crawl_error` pattern) rather than silently falling back to defaults.

## Sources

### Primary (HIGH confidence)
- Context7 `/unclecode/crawl4ai` — CrawlerRunConfig parameters, BrowserConfig parameters, clone/from_kwargs/to_dict methods, stealth/magic settings, fast text-mode crawling, configuration architecture overview
- Context7 `/unclecode/crawl4ai` — `docs/md_v2/api/parameters.md`, `docs/md_v2/core/browser-crawler-config.md`, `deploy/docker/c4ai-code-context.md` (source-level from_kwargs implementations)
- Phase 2 research (`02-RESEARCH.md`) — established `verbose=False` critical constraint, `_build_run_config()` pattern, hook pattern for per-request headers
- Existing `server.py` — confirmed `_build_run_config()` current signature and all fields already in use

### Secondary (MEDIUM confidence)
- Perplexity reasoning — BrowserConfig singleton immutability after `crawler.start()`, architectural split recommendation (CrawlerRunConfig-only profiles), Python dict merge best practice; aligned with Context7 official architecture docs
- Perplexity search — Python dict merge `{**a, **b, **c}` operator and `yaml.safe_load()` best practice (standard Python docs topic)

### Tertiary (LOW confidence)
- None — all critical claims verified against Context7 official docs or existing codebase

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — PyYAML via `yaml.safe_load()` is stdlib-adjacent; all crawl4ai fields verified against Context7 source-level docs
- Architecture: HIGH — singleton constraint verified against official crawl4ai architecture docs; merge pattern is standard Python
- Pitfalls: HIGH — `verbose=False` requirement is proven from Phase 1/2; BrowserConfig immutability confirmed by official docs; unit mismatch is concrete finding from reading `from_kwargs` source

**Research date:** 2026-02-19
**Valid until:** 2026-05-19 (crawl4ai 0.8.x pinned; stable until 0.9.x upgrade)
