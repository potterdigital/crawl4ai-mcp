# crawl4ai-mcp

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
![Python](https://img.shields.io/badge/python-3.12+-blue?logo=python&logoColor=white)
[![CI](https://github.com/potterdigital/crawl4ai-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/potterdigital/crawl4ai-mcp/actions/workflows/ci.yml)
[![crawl4ai](https://img.shields.io/badge/powered%20by-crawl4ai-orange)](https://crawl4ai.com)

An MCP server that gives AI assistants web crawling superpowers. Wraps [crawl4ai](https://docs.crawl4ai.com) (Playwright/Chromium) and exposes it as MCP tools — crawl pages, extract structured data, batch-crawl sitemaps, and manage browser sessions, all through the Model Context Protocol.

Works with any MCP-compatible client: [Claude Code](https://docs.anthropic.com/en/docs/claude-code), [Claude Desktop](https://claude.ai), [Cursor](https://cursor.com), [Windsurf](https://windsurf.com), [OpenAI Agents SDK](https://github.com/openai/openai-agents-python), and others.

## Why

AI coding assistants can't browse the web natively. This MCP server gives them a full Chromium-based crawler — handling JS-rendered pages, authenticated sessions, batch crawls, structured extraction, and sitemap ingestion. No API key required for basic crawling.

## Available Tools

| Tool                 | Description                                                                                                          |
| -------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `ping`               | Health check — confirms server and browser are running                                                               |
| `crawl_url`          | Crawl a URL and return clean markdown. Supports JS rendering, custom headers/cookies, CSS scoping, and cache control |
| `crawl_many`         | Crawl multiple URLs concurrently with configurable parallelism                                                       |
| `deep_crawl`         | BFS site crawl — follows links with configurable depth and page limits                                               |
| `crawl_sitemap`      | Crawl all URLs from an XML sitemap (supports gzip and sitemap indexes)                                               |
| `extract_structured` | LLM-powered structured JSON extraction with a user-defined schema                                                    |
| `extract_css`        | CSS-selector-based structured extraction — deterministic, no LLM required                                            |
| `create_session`     | Create a persistent browser session (preserves cookies and state)                                                    |
| `list_sessions`      | List all active browser sessions                                                                                     |
| `destroy_session`    | Destroy a named browser session                                                                                      |
| `list_profiles`      | List available crawl profiles and their settings                                                                     |
| `check_update`       | Check if a newer version of crawl4ai is available on PyPI                                                            |

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) — Python package manager

## Installation

```bash
# Clone the repository
git clone https://github.com/potterdigital/crawl4ai-mcp.git
cd crawl4ai-mcp

# Install dependencies (uv manages the virtualenv automatically)
uv sync

# Install Playwright browser (required by crawl4ai — downloads Chromium)
uv run crawl4ai-setup

# Verify the installation
uv run crawl4ai-doctor
```

## Register with Your MCP Client

### Claude Code

```bash
# Replace /path/to/crawl4ai-mcp with your actual clone path
claude mcp add-json --scope user crawl4ai '{
  "type": "stdio",
  "command": "uv",
  "args": [
    "run",
    "--directory",
    "/path/to/crawl4ai-mcp",
    "python",
    "-m",
    "crawl4ai_mcp.server"
  ]
}'
```

Verify with `claude mcp list` — `crawl4ai` should appear.

### Other MCP Clients

Add the following to your client's MCP server configuration:

```json
{
  "crawl4ai": {
    "type": "stdio",
    "command": "uv",
    "args": [
      "run",
      "--directory",
      "/path/to/crawl4ai-mcp",
      "python",
      "-m",
      "crawl4ai_mcp.server"
    ]
  }
}
```

Replace `/path/to/crawl4ai-mcp` with the absolute path to your clone. The `--directory` flag is required — without it, `uv run` looks for the virtualenv in the client's working directory.

## Usage Examples

### Basic Crawling

> "Crawl https://docs.python.org/3/library/asyncio.html and summarize the key concepts"

> "Fetch https://example.com with a custom Authorization header"

### JS-Heavy Pages

> "Crawl this page using the js_heavy profile and wait for #content to appear"

### Structured Extraction

> "Extract all product names and prices from this page as JSON using CSS selectors"

> "Use the LLM extractor to pull a list of API endpoints from this documentation page"

### Batch Crawling

> "Crawl all pages in this sitemap and summarize each one"

> "Deep crawl this docs site to depth 2 and find all pages mentioning authentication"

### Sessions

> "Create a browser session, log into this site, then crawl the dashboard page"

## Profiles

Four built-in profiles control crawler behavior. Use the `profile` parameter on any crawl tool, or call `list_profiles` to see all options.

| Profile    | Use Case                    | Key Settings                                                                |
| ---------- | --------------------------- | --------------------------------------------------------------------------- |
| `default`  | General-purpose crawling    | `domcontentloaded` wait, 60s timeout                                        |
| `fast`     | Static pages, quick fetches | `domcontentloaded` wait, 15s timeout, low word threshold                    |
| `js_heavy` | SPAs, lazy-loaded content   | `networkidle` wait, 90s timeout, full-page scroll, overlay removal          |
| `stealth`  | Anti-bot protected sites    | `networkidle` wait, 90s timeout, simulated user behavior, navigator masking |

Profiles are YAML files in `src/crawl4ai_mcp/profiles/`. You can add custom profiles there.

Merge order: `default` ← `named profile` ← `per-call overrides`

## Development

```bash
# Run the server directly (for debugging)
uv run python -m crawl4ai_mcp.server

# See server logs (stderr) while discarding MCP frames (stdout)
uv run python -m crawl4ai_mcp.server 2>&1 1>/dev/null

# Lint (catches print() calls that would corrupt stdio transport)
uv run ruff check src/

# Run tests
uv run pytest

# Diagnose crawl4ai / Playwright health
uv run crawl4ai-doctor
```

## Troubleshooting

**Tools don't appear in your MCP client**
Check that the `--directory` path in the registration command matches the actual project location. `uv run` without `--directory` looks for the virtualenv in the client's working directory, not this project.

**Server disconnects immediately**
Any output to stdout (from a `print()` call or `verbose=True` in crawl4ai config) corrupts the MCP stdio transport. Check stderr for the actual error:

```bash
uv run python -m crawl4ai_mcp.server 2>&1 1>/dev/null
```

**Chromium fails to start**
Run `uv run crawl4ai-doctor` to diagnose. If Playwright browsers are missing, run `uv run crawl4ai-setup` again.

**`extract_structured` returns an error about missing API key**
The LLM extraction tool requires a `provider` and corresponding API key (e.g., `OPENAI_API_KEY`). The `extract_css` tool is a free alternative that doesn't require an LLM.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and guidelines.

## License

This project is licensed under the Apache License 2.0 — see [LICENSE](LICENSE) for details.

This project uses [crawl4ai](https://github.com/unclecode/crawl4ai), which is also licensed under Apache 2.0.

## Acknowledgments

- [crawl4ai](https://crawl4ai.com) by [@unclecode](https://github.com/unclecode) — the crawling engine that powers this server
- [Model Context Protocol](https://modelcontextprotocol.io) — the protocol that makes this possible
- Built with [Claude Code](https://claude.ai/code)

---

Created by [Potter Digital](https://potterdigital.com)
