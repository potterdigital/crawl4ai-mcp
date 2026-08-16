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
| `ping`               | Health check — reports whether the browser is ready, installing, or unavailable                                      |
| `repair_browser`     | Install the Chromium build the crawler needs and start the browser, without restarting the server                    |
| `crawl_url`          | Crawl a URL and return clean markdown. Supports JS rendering, custom headers/cookies, CSS scoping, and cache control |
| `crawl_many`         | Crawl multiple URLs concurrently with configurable parallelism, politeness delays, and optional disk persistence     |
| `deep_crawl`         | BFS site crawl — follows links with configurable depth and page limits, politeness delays, and optional disk storage |
| `crawl_sitemap`      | Crawl all URLs from an XML sitemap (supports gzip and sitemap indexes, politeness delays, optional disk persistence) |
| `extract_structured` | LLM-powered structured JSON extraction with a user-defined schema                                                    |
| `extract_css`        | CSS-selector-based structured extraction — deterministic, no LLM required                                            |
| `create_session`     | Create a persistent browser session (preserves cookies and state)                                                    |
| `list_sessions`      | List all active browser sessions                                                                                     |
| `destroy_session`    | Destroy a named browser session                                                                                      |
| `list_profiles`      | List available crawl profiles and their settings                                                                     |
| `check_update`       | Check if a newer version of crawl4ai is available on PyPI                                                            |

Every tool ships MCP [tool annotations](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)
so your client can reason about it before calling:

- **Read-only:** `ping`, `list_profiles`, `list_sessions`, `check_update`. These
  inspect state and nothing else.
- **Destructive:** `destroy_session` only. It is the one tool that tears something
  down, discarding a session's page, cookies, and localStorage.
- **Everything else is non-destructive but not read-only.** The crawl and extract
  tools are deliberately *not* marked read-only, because each accepts a `js_code`
  parameter that runs caller-supplied JavaScript in the live page. If your client
  skips confirmation for read-only tools, you do not want that auto-approved
  against an arbitrary URL. They only ever add: a cache entry, a session page, or
  files under `output_dir`.

### Result shapes

`crawl_many`, `crawl_sitemap`, and `deep_crawl` return structured results with a
declared output schema, so you get real data in `structuredContent` rather than
prose to parse:

```jsonc
{
  "crawled": 2,          // pages that succeeded
  "total": 3,            // pages attempted
  "pages": [
    { "url": "https://example.com/a", "success": true, "markdown": "# Title\n\n..." },
    { "url": "https://example.com/b", "success": true, "markdown": "...", "depth": 1,
      "parent_url": "https://example.com/a" },        // depth/parent: deep_crawl only
    { "url": "https://example.com/c", "success": false, "error": "Connection timeout" }
  ],
  "output_dir": null,    // set when you passed output_dir
  "manifest": null,      // path to manifest.json, same condition
  "note": null           // e.g. that a sitemap was truncated at max_urls
}
```

Successes sort ahead of failures, and a failing URL never discards the pages that
worked. With `output_dir` set, each page carries `file` instead of `markdown` —
the content is on disk. That is the better mode for large content-heavy crawls,
since inline markdown comes back JSON-escaped.

`extract_css` returns `{ "url", "count", "items": [...], "error" }` with `items`
already parsed, not as a JSON string you decode twice.

`crawl_url` is unchanged and still returns plain markdown. A single page has no
tabular structure worth exposing, and wrapping it would only bury the content in
escaping.

### Progress on long crawls

Long-running tools (`crawl_many`, `crawl_sitemap`, `deep_crawl`, `repair_browser`)
report progress while they work. Clients abort a tool call that goes silent for too
long — Claude Code's default is 30 minutes on stdio — so a large crawl that emitted
nothing until it finished could be killed mid-flight. `deep_crawl` reports each page
as it completes; the others heartbeat every 15 seconds.

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

> "Crawl this list of URLs with a 1-second delay between requests to be respectful"

> "Batch crawl these URLs and save all results to /tmp/crawl_results as markdown files"

### Sessions

> "Create a browser session, log into this site, then crawl the dashboard page"

## Batch Crawling Options

All batch tools (`crawl_many`, `deep_crawl`, `crawl_sitemap`) support two optional parameters:

- **`delay`** (default: 0): Politeness delay in seconds between requests. Use this to respect target servers and avoid overwhelming them. For `deep_crawl`, this becomes `delay_before_return_html` in crawl4ai. For `crawl_many` and `crawl_sitemap`, this wires a RateLimiter into request dispatch.

- **`output_dir`** (default: None): Directory to write per-page `.md` files and a `manifest.json` instead of returning content inline. Useful for large batch crawls. When set, the tool returns a metadata summary with file paths instead of full page content.

Example:

```bash
# Crawl with politeness delay
crawl_many(urls=[...], delay=1.5)

# Crawl and save to disk
crawl_sitemap(sitemap_url="...", output_dir="/tmp/results")

# Both
deep_crawl(url="...", delay=0.5, output_dir="/tmp/crawl")
```

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

**Chromium fails to start / "Playwright Chromium binary is missing or stale"**
Most often happens after `uv sync` upgrades Playwright to a new version — the cached Chromium under `~/Library/Caches/ms-playwright/` (macOS) or `~/.cache/ms-playwright/` (Linux) is then missing the build Playwright expects.

The server handles this itself. It starts normally, reports the condition through `ping`, and installs the browser in the background, so crawling recovers without a restart. To force it from inside your MCP client, call the `repair_browser` tool. To fix it from a shell:

```bash
uv run crawl4ai-setup
```

Set `CRAWL4AI_MCP_AUTO_REPAIR=0` to disable the automatic install. Run `uv run crawl4ai-doctor` for a deeper diagnostic.

Note that a directory listing of the browser cache can be misleading: Claude Code's own bundled Playwright writes connection descriptor files into a `b/` subdirectory and launches the system Chrome, so the cache can look populated while containing no downloaded browsers at all. Only `chromium-<revision>/` directories count.

**The server never appears in the client at all**
The server is designed not to exit on a recoverable browser problem, so a true connect failure points elsewhere — usually a bad `--directory` path or a broken virtualenv. Run it by hand and read stderr:

```bash
uv run python -m crawl4ai_mcp.server 2>&1 1>/dev/null
```

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
