#!/usr/bin/env bash
set -euo pipefail

# ── Resolve project root ────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

echo "=== crawl4ai MCP — Update Script ==="
echo ""

# ── Show current installed version ──────────────────────────────────
CURRENT=$(uv run python -c "import importlib.metadata; print(importlib.metadata.version('crawl4ai'))")
echo "Current version: $CURRENT"

# ── Check PyPI for latest available version ─────────────────────────
LATEST=$(uv run python -c "
import httpx, json
resp = httpx.get('https://pypi.org/pypi/crawl4ai/json', timeout=10.0)
resp.raise_for_status()
print(resp.json()['info']['version'])
")
echo "Latest on PyPI: $LATEST"

# ── Early exit if already at latest ─────────────────────────────────
if [ "$CURRENT" = "$LATEST" ]; then
    echo ""
    echo "Already at latest version ($CURRENT)."
    exit 0
fi

# ── Upgrade crawl4ai within pyproject.toml constraints ──────────────
echo ""
echo "Upgrading crawl4ai..."
uv lock --upgrade-package crawl4ai
uv sync

# ── Show new installed version ──────────────────────────────────────
NEW=$(uv run python -c "import importlib.metadata; print(importlib.metadata.version('crawl4ai'))")
echo "New version: $NEW"

# ── Detect pin range block ──────────────────────────────────────────
if [ "$NEW" = "$CURRENT" ] && [ "$NEW" != "$LATEST" ]; then
    echo ""
    echo "NOTE: Latest version ($LATEST) exceeds your pyproject.toml version constraint."
    echo "To upgrade beyond the current pin, edit pyproject.toml and widen the crawl4ai version range,"
    echo "then re-run this script."
    exit 0
fi

# ── Reinstall Playwright browser ────────────────────────────────────
echo ""
echo "Reinstalling Playwright browser..."
uv run crawl4ai-setup

# ── Smoke test ──────────────────────────────────────────────────────
echo ""
echo "Running smoke test..."
uv run python -c "
import crawl4ai
print(f'crawl4ai {crawl4ai.__version__} imported successfully')
from crawl4ai import AsyncWebCrawler, BrowserConfig
print('Core imports OK')
"

# ── Done ────────────────────────────────────────────────────────────
echo ""
echo "=== Update complete: $CURRENT -> $NEW ==="
echo "Restart the MCP server for changes to take effect."
