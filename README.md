# crawl4ai MCP Server

A local MCP server that gives Claude Code the ability to crawl web pages and extract content using [crawl4ai](https://docs.crawl4ai.com). Claude can crawl any URL, extract structured data, and orchestrate multi-page crawls — all through MCP tool calls, without leaving the coding session.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) — Python package manager
- Claude Code CLI (`claude`)

## Installation

```bash
# Clone the repository
git clone <repo-url> /Users/brianpotter/ai_tools/crawl4ai_mcp
cd /Users/brianpotter/ai_tools/crawl4ai_mcp

# Install dependencies (uv manages the virtualenv automatically)
uv sync

# Install Playwright browser (required by crawl4ai — downloads Chromium)
uv run crawl4ai-setup

# Verify the installation
uv run crawl4ai-doctor
```

## Register with Claude Code

Register the server as a global MCP server so it is available in all Claude Code sessions:

```bash
claude mcp add-json --scope user crawl4ai '{
  "type": "stdio",
  "command": "uv",
  "args": [
    "run",
    "--directory",
    "/Users/brianpotter/ai_tools/crawl4ai_mcp",
    "python",
    "-m",
    "crawl4ai_mcp.server"
  ]
}'
```

### Verify Registration

```bash
# List registered MCP servers — crawl4ai should appear
claude mcp list
```

### Manual Registration (Alternative)

If you prefer to edit `~/.claude.json` directly, add the following under `mcpServers`:

```json
{
  "crawl4ai": {
    "type": "stdio",
    "command": "uv",
    "args": [
      "run",
      "--directory",
      "/Users/brianpotter/ai_tools/crawl4ai_mcp",
      "python",
      "-m",
      "crawl4ai_mcp.server"
    ]
  }
}
```

## Available Tools

| Tool | Description |
|------|-------------|
| `ping` | Verify the server is running and the browser is ready |

More tools are added in Phase 2 (crawl), Phase 3 (profiles), Phase 4 (extraction), and Phase 5 (multi-page crawl).

## How It Works

The server uses [FastMCP](https://github.com/modelcontextprotocol/python-sdk) (stdio transport) and manages a single `AsyncWebCrawler` (Chromium via Playwright) that starts at server boot and is reused across all tool calls. This means:

- No per-request browser startup cost
- No orphaned browser processes after shutdown
- All server logging goes to stderr — stdout carries only MCP protocol frames

## Development

```bash
# Run the server directly (for debugging — MCP Inspector recommended for interactive testing)
uv run python -m crawl4ai_mcp.server

# Lint
uv run ruff check src/

# Test
uv run pytest
```

## Troubleshooting

**Tools don't appear in Claude Code**
Check that the `--directory` path in the registration command matches the actual project location. `uv run` without `--directory` looks for the virtualenv in Claude Code's working directory, not this project.

**Server disconnects immediately**
Any output to stdout (from a `print()` call or `verbose=True` in crawl4ai config) corrupts the MCP stdio transport. Check stderr for the actual error: `uv run python -m crawl4ai_mcp.server 2>&1 1>/dev/null`.

**Chromium fails to start**
Run `uv run crawl4ai-doctor` to diagnose. If Playwright browsers are missing, run `uv run crawl4ai-setup` again.
