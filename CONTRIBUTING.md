# Contributing

Contributions are welcome. This is a focused tool — keep changes small and purposeful.

## Development Setup

```bash
git clone https://github.com/potterdigital/crawl4ai-mcp.git
cd crawl4ai-mcp

# Install dependencies (uv manages the virtualenv)
uv sync

# Install Playwright browser (Chromium — required by crawl4ai)
uv run crawl4ai-setup

# Verify everything works
uv run pytest
uv run ruff check src/
```

## The stdout Constraint

This is the single most important thing to understand about this codebase.

The MCP `stdio` transport uses stdout exclusively for JSON-RPC frames. **Any stray output to stdout** — from a `print()` call, `verbose=True`, or a chatty library — immediately corrupts the protocol and disconnects the client.

Rules:

- No `print()` calls anywhere. The ruff `T201` rule enforces this — do not disable it.
- All logging goes to `stderr` via the `logging` module.
- `BrowserConfig(verbose=False)` is hardcoded. Do not change it.
- Test any new dependency for stdout pollution before merging.

## Making Changes

1. Fork the repo and create a branch from `main`
2. Make your changes
3. Run `uv run pytest` — all tests must pass
4. Run `uv run ruff check src/` — must be clean
5. Open a PR with a clear description of what changed and why

## What Belongs Here

- Bug fixes
- Compatibility updates for new crawl4ai releases
- Documentation improvements
- New built-in profiles (`src/crawl4ai_mcp/profiles/`)
- New MCP tools that extend crawling capabilities

## What Doesn't Belong Here

- New dependencies beyond crawl4ai and mcp (without discussion first)
- Changes to the stdio transport model
- Anything that writes to stdout
