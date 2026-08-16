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
| `deep_crawl`         | BFS site crawl — follows links with configurable depth, page limits, domain allow/block lists, and optional disk storage |
| `crawl_sitemap`      | Crawl all URLs from an XML sitemap (supports gzip and sitemap indexes, politeness delays, optional disk persistence) |
| `extract_structured` | LLM-powered structured JSON extraction with a user-defined schema                                                    |
| `extract_css`        | CSS **or XPath** selector-based structured extraction — deterministic, no LLM required                               |
| `extract_patterns`   | Regex extraction of emails, phones, prices, dates, URLs and more — no LLM, no schema, no cost                        |
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

If the whole operation fails before any page is attempted (an unreachable or
non-XML sitemap, say), you get `crawled: 0` and an `error` explaining why,
rather than an exception.

Note that `success` means the page was **retrieved**, not that the server was
happy: crawl4ai reports success for anything it managed to fetch, so an HTTP
404 arrives with `success: true` and the error page's body. Check `status_code`
before treating content as real.

`crawl_url` is unchanged and still returns plain markdown. A single page has no
tabular structure worth exposing, and wrapping it would only bury the content in
escaping.

### Links and tables

`crawl_many`, `crawl_sitemap`, and `deep_crawl` take `include_links` and
`include_tables`. crawl4ai collects both on every crawl; they are returned only
when asked for, because they routinely outweigh the page content — one Wikipedia
article carries 997 internal links, about 130KB of JSON.

```jsonc
{
  "url": "https://en.wikipedia.org/wiki/...",
  "success": true,
  "markdown": "...",
  "links": {
    "internal": [{ "href": "...", "text": "Jump to content", "title": null }],
    "external": [{ "href": "...", "text": "Donate", "title": null }]
  },
  "tables": [
    { "headers": ["Country", "Population"],
      "rows": [["India", "1,428,627,663"]],
      "caption": "List of countries by population" }
  ]
}
```

Only tables crawl4ai scored as real tabular data are included, so page-layout
tables are already filtered out. Nothing is truncated when the flags are on, and
requested links and tables still come back inline when `output_dir` is set —
only markdown goes to disk.

### When a crawl fails

Errors carry the diagnostics crawl4ai already collected, not just a status code.
crawl4ai retries behind its own anti-bot detection, so "blocked" can mean one
refusal or several across a proxy and an HTTP fallback:

```
Crawl failed
URL: https://httpbin.org/status/429
HTTP status: 429
Error: Blocked by anti-bot protection: HTTP 429 Too Many Requests
Attempts: 1, 1 blocked
Blocked by: HTTP 429 Too Many Requests
```

You also get `Redirected to:` when the crawl ended up somewhere other than where
you pointed it (a login wall is the usual reason), and `Retry-After:` when the
server sent one. These lines are omitted when there is nothing non-obvious to
report, so an ordinary timeout stays a one-line error. The same detail appears in
each failed page's `error` in a batch crawl.

### Extracting with XPath

`extract_css` takes `selector_type="xpath"` and the identical schema shape, so
you can reach what CSS cannot — matching on text content, or walking to a parent
or preceding sibling:

```
extract_css(url="https://www.python.org/about/",
            selector_type="xpath",
            schema={"name": "PyLinks",
                    "baseSelector": "//a[contains(text(), 'Python')]",
                    "fields": [{"name": "text", "selector": ".", "type": "text"}]})
```

Prefer CSS otherwise; it is shorter and more people can read it. With
`selector_type="xpath"`, start relative field selectors with `./` or `.//` — a
leading `//` searches the whole document again rather than inside the matched
item.

### Crawling more than one host

`deep_crawl`'s `scope` cannot express "this docs site *and* its separate API
host". `allowed_domains` can:

```
deep_crawl(url="https://docs.example.com",
           allowed_domains=["docs.example.com", "api.example.com"])
```

Setting `allowed_domains` **replaces** `scope` as the boundary of the crawl, and
is stricter than it: nothing outside the listed hosts is followed. That is
forced by crawl4ai's ordering — it discards off-domain links before any filter
runs, so an allowlist can only work once external links are let through.

List every host you want crawled, **including the start URL's own**. Leaving it
out is legal and does something surprising: the start page is still fetched, but
every link back into its own site is dropped and only the listed hosts are
traversed. The result carries a `note` when that happens.

`blocked_domains` only subtracts, so it composes with any `scope` without
widening anything. Subdomains of a listed domain are included in both lists,
matching crawl4ai's rule everywhere else.

### Filtering a page to what you asked for

Every crawl tool takes an optional `query`. It swaps the default density-based
filter for BM25 relevance scoring, so the page comes back reduced to the parts
matching your question — before the tokens are spent, with no LLM involved:

```
crawl_url(url="https://docs.astral.sh/uv/concepts/cache/",
          query="how do I clear or prune the cache")
```

On that page this returns roughly 23% of the full text, and it is the right 23%.

`deep_crawl` additionally takes `strategy="best-first"` with
`relevance_keywords=[...]`, which spends the `max_pages` budget on the pages
most likely to matter rather than whatever is shallowest.

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

## Architecture Notes

[`docs/crawl4ai-boundary.md`](docs/crawl4ai-boundary.md) explains which parts of
this server are hand-rolled and why, verified against crawl4ai 0.9.2 — worth
reading before assuming something should just call upstream.

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
