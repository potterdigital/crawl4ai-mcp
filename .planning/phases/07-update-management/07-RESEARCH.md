# Phase 7: Update Management - Research

**Researched:** 2026-02-22
**Domain:** PyPI version checking, importlib.metadata, shell scripting (uv/Playwright), MCP server startup hooks
**Confidence:** HIGH

## Summary

Phase 7 adds three capabilities: (1) a `check_update` MCP tool that queries PyPI for the latest crawl4ai version and compares it to the installed version, (2) a startup warning logged to stderr when the server detects it is running an outdated version, and (3) a `scripts/update.sh` shell script that performs the actual offline upgrade. The critical constraint is that the tool must NEVER perform upgrades in-process -- it only reports.

All required libraries are already available in the project's dependency tree: `httpx` (0.28.1) for async HTTP to PyPI, `importlib.metadata` (stdlib) for installed version lookup, and `packaging` (26.0, transitive dep) for semantic version comparison. No new dependencies are needed.

**Primary recommendation:** Implement `check_update` as an async tool in `server.py` (alongside existing tools -- no `tools/admin.py` split needed yet since it is a single tool). Use `asyncio.create_task()` for a fire-and-forget startup check in `app_lifespan` that logs to stderr without blocking browser initialization.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| UPDT-01 | check_update MCP tool: queries PyPI, compares versions, shows changelog summary | PyPI JSON API at `https://pypi.org/pypi/crawl4ai/json` returns `info.version` for latest; `importlib.metadata.version('crawl4ai')` for installed; `packaging.version.Version` for comparison; CHANGELOG.md is parseable via regex |
| UPDT-02 | Server logs stderr warning on startup if outdated | Non-blocking `asyncio.create_task()` in `app_lifespan` with httpx async check; 5s timeout; logs via existing `logger.warning()` to stderr |
| UPDT-03 | scripts/update.sh: safe offline upgrade with playwright reinstall and smoke test | `uv lock --upgrade-package crawl4ai && uv sync` for upgrade; `uv run crawl4ai-setup` for Playwright; `uv run python -c "import crawl4ai; print(crawl4ai.__version__)"` for smoke test |
</phase_requirements>

## Standard Stack

### Core (Already Available -- No New Dependencies)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `httpx` | 0.28.1 | Async HTTP client for PyPI API and GitHub API | Already imported in server.py for sitemap fetching; async-native |
| `importlib.metadata` | stdlib | Get installed package version | Python 3.12+ stdlib; no external dep needed; replaces deprecated pkg_resources |
| `packaging` | 26.0 | Semantic version comparison | Already in dependency tree (transitive); provides `Version` class with proper PEP 440 comparison |
| `asyncio` | stdlib | Background task for startup check | Already used throughout the server |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `re` | stdlib | Parse CHANGELOG.md sections | Extract version-specific changelog section for display |
| `logging` | stdlib | Startup warning to stderr | Already configured in server.py; `logger.warning()` goes to stderr |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `packaging.version.Version` | Simple string split/compare | packaging handles pre-releases, epochs, post-releases correctly; string compare fails on `0.9.0 < 0.10.0` |
| PyPI JSON API | `pip index versions crawl4ai` | Shell subprocess is slower, harder to parse, not available in all envs |
| GitHub Releases API for changelog | Raw CHANGELOG.md from GitHub | CHANGELOG.md has structured sections per version; GitHub release body just says "see CHANGELOG.md" |
| `aiohttp` for HTTP | `httpx` | httpx already imported; no reason to add another HTTP library |

**Installation:**
```bash
# No new packages needed -- all already available
```

## Architecture Patterns

### Recommended Module Placement

The CLAUDE.md planned module structure shows `tools/admin.py` for `check_update` and `list_profiles`. However, since `list_profiles` is already in `server.py` and `check_update` is the only new tool, adding it directly to `server.py` is simpler. The `tools/` split can happen in a future refactor when the file grows too large.

```
src/crawl4ai_mcp/
├── server.py           # Add check_update tool + _check_for_updates() startup helper
scripts/
└── update.sh           # Offline upgrade script (new)
```

### Pattern 1: check_update Tool (PyPI JSON API + Version Compare)

**What:** Async tool that fetches latest version from PyPI, compares to installed, optionally fetches changelog.
**When to use:** Called by Claude mid-session to check if crawl4ai has updates.

```python
import importlib.metadata
from packaging.version import Version

@mcp.tool()
async def check_update(ctx: Context[ServerSession, AppContext]) -> str:
    """Check if a newer version of crawl4ai is available on PyPI.

    Compares the installed version against the latest release on PyPI.
    Reports version info and changelog highlights. Never performs the
    upgrade itself -- use scripts/update.sh for that.
    """
    installed = importlib.metadata.version("crawl4ai")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get("https://pypi.org/pypi/crawl4ai/json")
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, httpx.TimeoutException) as e:
        return (
            f"Version check failed\n"
            f"Installed: {installed}\n"
            f"Error: Could not reach PyPI: {e}"
        )

    latest = data["info"]["version"]
    installed_ver = Version(installed)
    latest_ver = Version(latest)

    if latest_ver <= installed_ver:
        return (
            f"crawl4ai is up to date\n"
            f"Installed: {installed}\n"
            f"Latest: {latest}"
        )

    # Fetch changelog summary
    changelog_summary = await _fetch_changelog_summary(latest)

    return (
        f"Update available\n"
        f"Installed: {installed}\n"
        f"Latest: {latest}\n"
        f"Release: https://github.com/unclecode/crawl4ai/releases/tag/v{latest}\n\n"
        f"To update, stop the server and run:\n"
        f"  cd {_project_dir()} && scripts/update.sh\n\n"
        f"{changelog_summary}"
    )
```

### Pattern 2: Non-Blocking Startup Check (fire-and-forget asyncio.create_task)

**What:** During `app_lifespan`, after the crawler is ready, fire off a background task that checks PyPI and logs a warning if outdated.
**When to use:** Every server startup. Must not block or delay the server becoming ready.

```python
async def _startup_version_check() -> None:
    """Background task: check PyPI for newer crawl4ai version, log warning if outdated."""
    try:
        installed = importlib.metadata.version("crawl4ai")
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get("https://pypi.org/pypi/crawl4ai/json")
            resp.raise_for_status()
            data = resp.json()

        latest = data["info"]["version"]
        if Version(latest) > Version(installed):
            logger.warning(
                "crawl4ai update available: %s -> %s "
                "(run scripts/update.sh to upgrade)",
                installed, latest,
            )
    except Exception:
        # Never let version check failure affect server startup
        pass


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    # ... existing crawler startup ...
    logger.info("Browser ready")

    # Fire-and-forget version check (does not block startup)
    asyncio.create_task(_startup_version_check())

    try:
        yield AppContext(crawler=crawler, profile_manager=profile_manager)
    finally:
        await crawler.close()
```

### Pattern 3: Structured Error Returns (Existing Pattern)

**What:** All tools return structured strings (not exceptions) so Claude can reason about failures.
**When to use:** All error paths in check_update.

This follows the established `_format_crawl_error()` pattern already in server.py. The check_update tool returns structured text for all outcomes: up-to-date, update available, check failed.

### Anti-Patterns to Avoid

- **Never `pip install` in-process:** The tool must NEVER call pip, uv, or any package installer. It only reports.
- **Never block startup on PyPI check:** If PyPI is down or slow, the server must still start normally. Use fire-and-forget with aggressive timeout.
- **Never write to stdout:** All version check logging goes to stderr via the existing `logger`.
- **Never trust PyPI data blindly:** Validate that `info.version` is a valid version string before comparing.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Version comparison | String split/compare | `packaging.version.Version` | Handles pre-releases, post-releases, epochs, dev versions per PEP 440 |
| Installed version lookup | Parse site-packages, read setup.py | `importlib.metadata.version()` | Stdlib, reliable, handles editable installs |
| HTTP to PyPI | `urllib.request`, subprocess curl | `httpx.AsyncClient` | Already in project, async-native, proper timeout handling |
| Package upgrade | `subprocess.run(["pip", "install", ...])` | Shell script (`scripts/update.sh`) | Must happen offline; in-process pip install can corrupt running server |

**Key insight:** The version checking domain is well-served by stdlib + existing deps. The only custom code is the changelog extraction (parsing a well-structured markdown file) and the glue logic.

## Common Pitfalls

### Pitfall 1: Blocking Startup on Network Call
**What goes wrong:** Making an `await httpx.get()` call in `app_lifespan` before `yield` means the server won't accept tool calls until the PyPI check completes (or times out).
**Why it happens:** Natural instinct is to await the check in the lifespan setup.
**How to avoid:** Use `asyncio.create_task()` to fire-and-forget. The task runs concurrently while the server starts accepting calls.
**Warning signs:** Server takes 5+ seconds to start when PyPI is slow.

### Pitfall 2: Startup Check Crashing the Server
**What goes wrong:** An unhandled exception in the background version check task propagates and kills the server.
**Why it happens:** `asyncio.create_task()` exceptions are only raised when the task is awaited -- but if the task fails loudly (e.g., via a logging call that itself fails), it can disrupt.
**How to avoid:** Wrap the entire background check in a broad `try/except Exception: pass`. Version checking is informational; it must never affect server operation.
**Warning signs:** Server crashes on networks without internet access.

### Pitfall 3: Stale importlib.metadata After In-Process Upgrade
**What goes wrong:** If someone somehow runs pip mid-session, `importlib.metadata.version()` may return cached/stale results.
**Why it happens:** importlib.metadata reads from the filesystem but may cache distribution info.
**How to avoid:** Not a real concern for this project -- we never upgrade in-process. The update script requires a server restart, which reloads everything.

### Pitfall 4: Version Pin Blocking Upgrades
**What goes wrong:** `pyproject.toml` has `crawl4ai>=0.8.0,<0.9.0`. Running `uv lock --upgrade-package crawl4ai` will not upgrade past 0.8.x.
**Why it happens:** The version constraint in pyproject.toml takes precedence.
**How to avoid:** The `update.sh` script should first widen the constraint (e.g., change `<0.9.0` to `<1.0.0`) or use `uv add "crawl4ai>=0.8.0"` to remove the upper bound before upgrading. Alternatively, the script can accept a `--version` flag. The check_update tool should note when the latest version exceeds the pin range.
**Warning signs:** `uv lock --upgrade-package` reports "already at latest" but check_update shows a newer version exists.

### Pitfall 5: CHANGELOG.md Fetch Failing
**What goes wrong:** GitHub raw content may be rate-limited or unavailable; the changelog URL might change.
**Why it happens:** External dependency on GitHub for changelog content.
**How to avoid:** Make changelog fetching optional -- if it fails, still return version comparison result. Never let changelog fetch failure prevent the version check from completing.
**Warning signs:** check_update returns partial results or errors when GitHub is down.

## Code Examples

### Fetching Latest Version from PyPI (Verified)

```python
# Source: Verified against live PyPI API (https://pypi.org/pypi/crawl4ai/json)
# Response structure: {"info": {"version": "0.8.0", ...}, "releases": {...}, ...}

async def _get_latest_pypi_version() -> tuple[str, dict]:
    """Fetch latest crawl4ai version and full PyPI metadata."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get("https://pypi.org/pypi/crawl4ai/json")
        resp.raise_for_status()
        data = resp.json()
    return data["info"]["version"], data
```

### Getting Installed Version (Verified)

```python
# Source: Python 3.12 stdlib docs (https://docs.python.org/3/library/importlib.metadata.html)
import importlib.metadata

installed = importlib.metadata.version("crawl4ai")  # Returns "0.8.0"
# Synchronous call, ~1ms, reads from .dist-info in site-packages
```

### Version Comparison (Verified)

```python
# Source: packaging docs (https://packaging.pypa.io/en/stable/version.html)
from packaging.version import Version

installed_ver = Version("0.8.0")
latest_ver = Version("0.8.1")

if latest_ver > installed_ver:
    print(f"Update available: {installed_ver} -> {latest_ver}")

# Correctly handles: pre-releases, post-releases, dev versions
# Version("0.9.0a1").is_prerelease == True
# Version("0.8.0") < Version("0.10.0") == True (unlike string compare)
```

### Extracting Changelog Section (Verified)

```python
# Source: Verified against https://raw.githubusercontent.com/unclecode/crawl4ai/main/CHANGELOG.md
# Format follows Keep a Changelog: ## [version] - date, ### Category, - **Item**: desc
import re

async def _fetch_changelog_summary(version: str) -> str:
    """Fetch and extract changelog section for a specific version."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://raw.githubusercontent.com/unclecode/crawl4ai/main/CHANGELOG.md"
            )
            resp.raise_for_status()
    except (httpx.HTTPError, httpx.TimeoutException):
        return "Changelog: https://github.com/unclecode/crawl4ai/blob/main/CHANGELOG.md"

    text = resp.text
    # Extract the section for the target version
    escaped = re.escape(version)
    pattern = rf"## \[{escaped}\].*?\n(.*?)(?=\n## \[|$)"
    match = re.search(pattern, text, re.DOTALL)
    if not match:
        return f"Changelog: https://github.com/unclecode/crawl4ai/blob/main/CHANGELOG.md"

    section = match.group(1).strip()
    # Extract category headers and first-level bullets for a summary
    lines = []
    for line in section.splitlines():
        if line.startswith("### ") or line.strip().startswith("- **"):
            lines.append(line)
    # Truncate to reasonable length
    summary = "\n".join(lines[:20])
    if len(lines) > 20:
        summary += f"\n... and {len(lines) - 20} more items"
    return f"Changelog highlights for v{version}:\n{summary}"
```

### update.sh Script Pattern (Verified)

```bash
#!/usr/bin/env bash
# Source: uv docs (https://docs.astral.sh/uv/concepts/projects/dependencies/)
# Verified: uv lock --upgrade-package + uv sync pattern

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

echo "=== crawl4ai MCP Server Update ==="
echo ""

# 1. Show current version
CURRENT=$(uv run python -c "import importlib.metadata; print(importlib.metadata.version('crawl4ai'))")
echo "Current version: $CURRENT"

# 2. Upgrade crawl4ai within pyproject.toml constraints
echo ""
echo "Upgrading crawl4ai..."
uv lock --upgrade-package crawl4ai
uv sync

# 3. Show new version
NEW=$(uv run python -c "import importlib.metadata; print(importlib.metadata.version('crawl4ai'))")
echo "New version: $NEW"

if [ "$CURRENT" = "$NEW" ]; then
    echo ""
    echo "Already at latest version within constraints."
    echo "If a newer version exists outside the pin range, edit pyproject.toml first."
    exit 0
fi

# 4. Reinstall Playwright browser (crawl4ai may depend on newer Playwright)
echo ""
echo "Reinstalling Playwright browser..."
uv run crawl4ai-setup

# 5. Smoke test: verify import and version
echo ""
echo "Running smoke test..."
uv run python -c "
import crawl4ai
print(f'crawl4ai {crawl4ai.__version__} imported successfully')
from crawl4ai import AsyncWebCrawler, BrowserConfig
print('Core imports OK')
"

echo ""
echo "=== Update complete: $CURRENT -> $NEW ==="
echo "Restart the MCP server for changes to take effect."
```

### Non-Blocking Startup Check (Pattern)

```python
import asyncio

async def _startup_version_check() -> None:
    """Background task: warn on stderr if crawl4ai is outdated."""
    try:
        installed = importlib.metadata.version("crawl4ai")
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get("https://pypi.org/pypi/crawl4ai/json")
            resp.raise_for_status()
            data = resp.json()

        latest = data["info"]["version"]
        if Version(latest) > Version(installed):
            logger.warning(
                "A newer crawl4ai version is available: %s (installed: %s). "
                "Run scripts/update.sh to upgrade.",
                latest, installed,
            )
    except Exception:
        # Version check is informational -- never disrupt server startup
        pass

# In app_lifespan, AFTER crawler is ready:
asyncio.create_task(_startup_version_check())
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `pkg_resources.get_distribution()` | `importlib.metadata.version()` | Python 3.8+ | pkg_resources is deprecated; importlib.metadata is stdlib and faster |
| `pip install --upgrade` in scripts | `uv lock --upgrade-package + uv sync` | uv 0.1+ (2024) | uv is 10-100x faster, respects lock files, better constraint handling |
| Manual Playwright install | `crawl4ai-setup` command | crawl4ai 0.6+ | crawl4ai ships a setup command that handles Playwright browser installation |
| `requests` for HTTP | `httpx` async client | httpx 0.20+ | httpx is already in the project; native async support eliminates thread pool need |

**Deprecated/outdated:**
- `pkg_resources`: Deprecated since Python 3.12. Use `importlib.metadata` instead.
- `pip install` in uv projects: Use `uv lock --upgrade-package + uv sync` to stay within the uv ecosystem.

## Open Questions

1. **Version Pin Strategy for update.sh**
   - What we know: pyproject.toml currently pins `crawl4ai>=0.8.0,<0.9.0`. `uv lock --upgrade-package` respects this constraint and will not upgrade to 0.9.0+.
   - What's unclear: Should update.sh automatically widen the constraint, or should it warn the user and let them decide?
   - Recommendation: Have update.sh detect when the latest version exceeds the pin range and print a clear message explaining that the user needs to update pyproject.toml. Do not auto-edit pyproject.toml -- that is a user decision about compatibility.

2. **Changelog Extraction Reliability**
   - What we know: CHANGELOG.md follows Keep a Changelog format consistently across all 13 releases. The regex extraction works reliably on the current format.
   - What's unclear: Whether the format will stay stable in future releases.
   - Recommendation: Make changelog extraction a best-effort helper with graceful fallback to a URL link. If parsing fails, still return version comparison results.

3. **Rate Limiting on PyPI/GitHub**
   - What we know: PyPI JSON API has no documented rate limit for simple GET requests. GitHub raw content has generous limits for unauthenticated requests.
   - What's unclear: Whether aggressive CI/CD environments might hit limits.
   - Recommendation: Not a concern for local MCP server (one check per startup + occasional tool calls). No caching needed.

## Sources

### Primary (HIGH confidence)
- PyPI JSON API for crawl4ai -- verified live response structure: `https://pypi.org/pypi/crawl4ai/json`
- Python importlib.metadata docs: `https://docs.python.org/3/library/importlib.metadata.html`
- packaging.version docs: `https://packaging.pypa.io/en/stable/version.html`
- uv dependency management docs: `https://docs.astral.sh/uv/concepts/projects/dependencies/`
- crawl4ai CHANGELOG.md (raw): `https://raw.githubusercontent.com/unclecode/crawl4ai/main/CHANGELOG.md`
- crawl4ai installation docs: `https://docs.crawl4ai.com/core/installation/`

### Secondary (MEDIUM confidence)
- GitHub Releases API for crawl4ai: `https://api.github.com/repos/unclecode/crawl4ai/releases/latest` -- release body just links to CHANGELOG.md, not useful for inline display

### Tertiary (LOW confidence)
- None. All findings verified against live APIs and documentation.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all libraries verified available in current environment, no new dependencies
- Architecture: HIGH -- patterns follow established server.py conventions, verified with working code
- Pitfalls: HIGH -- tested version comparison, PyPI API, changelog parsing against live data
- update.sh: MEDIUM -- uv upgrade commands verified in docs but not test-executed (would modify lock file)

**Research date:** 2026-02-22
**Valid until:** 2026-03-22 (stable domain -- PyPI API is versioned, stdlib is stable)
