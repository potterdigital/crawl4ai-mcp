---
phase: 01-foundation
plan: 03
subsystem: infra
tags: [readme, mcp-registration, documentation, claude-code, claude-mcp]

# Dependency graph
requires:
  - phase: 01-01
    provides: FastMCP scaffold with stderr-only logging
  - phase: 01-02
    provides: AsyncWebCrawler singleton with ping tool

provides:
  - README.md with copy-pasteable installation and registration instructions
  - Server registered as global user-scoped MCP server in Claude Code (~/.claude.json)
  - Verified end-to-end: server returns valid JSON-RPC on stdout via uv --directory invocation

affects: [all future phases — foundation complete]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "uv run --directory <abs-path>: required so uv finds the venv from any Claude Code working directory"
    - "claude mcp add-json --scope user: registers server in ~/.claude.json for all sessions"

key-files:
  created:
    - README.md
  modified: []

key-decisions:
  - "--scope user confirmed valid (not --scope global) — claude mcp add-json --help shows: local, user, or project"
  - "Registration goes to ~/.claude.json not in-repo .mcp.json — user-scoped, survives project changes"
  - "No Task 2 commit needed — registration modifies ~/.claude.json which is outside the repo"

requirements-completed: [INFRA-01, INFRA-05]

# Metrics
duration: 1min
completed: 2026-02-20
---

# Phase 1 Plan 3: README and Server Registration Summary

**README written with copy-pasteable claude mcp add-json --scope user command and raw JSON config; server registered in ~/.claude.json and verified via JSON-RPC stdout smoke test — Phase 1 complete**

## Performance

- **Duration:** ~1 min
- **Started:** 2026-02-20T04:47:16Z
- **Completed:** 2026-02-20T04:48:52Z
- **Tasks:** 2 (1 documentation + 1 registration/verification)
- **Files created:** 1 (README.md)

## Accomplishments

- Written README.md with all required sections: prerequisites, installation, registration command, manual JSON config, tool table, how it works, development, troubleshooting
- Confirmed `--scope user` is the correct scope flag (not `--scope global`) via `claude mcp add-json --help`
- Registered server as global user-scoped MCP server in `~/.claude.json`
- Verified `crawl4ai` appears in `~/.claude.json` `mcpServers` with correct command, `--directory`, and module path
- Full stdout smoke test passed: `uv run --directory` invocation returns valid JSON-RPC from server

## Task Commits

Each task was committed atomically:

1. **Task 1: Write README.md** - `4f4dbfd` (docs)
2. **Task 2: Register server** - no commit (registration modifies `~/.claude.json`, outside the repo — nothing to stage)

## README Sections Written

| Section | Purpose |
|---------|---------|
| Prerequisites | Python 3.12+, uv, Claude Code CLI |
| Installation | git clone, uv sync, crawl4ai-setup, crawl4ai-doctor |
| Register with Claude Code | Copy-pasteable `claude mcp add-json --scope user` command |
| Verify Registration | `claude mcp list` |
| Manual Registration | Raw JSON config block for `~/.claude.json` |
| Available Tools | Tool table (ping), forward references to Phase 2-5 |
| How It Works | FastMCP stdio, singleton crawler, stderr-only logging |
| Development | Direct run, lint, test commands |
| Troubleshooting | stdout corruption, Chromium startup, --directory issues |

## Exact Registration Command Used

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

Scope flag `--scope user` confirmed correct via `claude mcp add-json --help` output: `(local, user, or project)`. No deviation from plan.

## Registration Verification

`~/.claude.json` mcpServers entry verified:

```json
{
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
```

Registered servers in `~/.claude.json`: `['context7', 'perplexity', 'playwright', 'serena', 'crawl4ai']`

Note: `claude mcp list` produces no output in non-TTY (subprocess) context — likely writes only to a TTY. The `~/.claude.json` direct check confirmed registration.

## Stdout Smoke Test Result

```
PASS: Phase 1 complete — server returns valid JSON-RPC on stdout
```

Invocation: `uv run --directory /Users/brianpotter/ai_tools/crawl4ai_mcp python -m crawl4ai_mcp.server`
Input: MCP `initialize` JSON-RPC request
Output: Valid JSON-RPC response — confirms full stack operational.

## Deviations from Plan

None - plan executed exactly as written. `--scope user` confirmed as correct flag (matched plan's expected value).

## Phase 1 Success Criteria Verification

All 5 success criteria from ROADMAP.md confirmed met:

| Criterion | Status | Evidence |
|-----------|--------|---------|
| README.md with copy-pasteable `claude mcp add-json` command containing `--scope user`, `--directory`, absolute path | PASS | README.md line 33 |
| README.md contains raw JSON config block for manual `~/.claude.json` editing | PASS | README.md lines 56-72, `mcpServers` present |
| `claude mcp list` shows crawl4ai (or `~/.claude.json` mcpServers contains `crawl4ai`) | PASS | Python JSON verification confirmed |
| Registration command uses correct absolute path `/Users/brianpotter/ai_tools/crawl4ai_mcp` | PASS | Verified in `~/.claude.json` |
| Stdout smoke test via `uv run --directory` passes | PASS | Valid JSON-RPC on stdout |

## Phase 1 Complete

Phase 1 (Foundation) is complete. All 3 plans executed:

- **01-01**: FastMCP scaffold, pyproject.toml, stderr-only logging, stub ping tool
- **01-02**: AsyncWebCrawler singleton via lifespan, BrowserConfig(verbose=False), `_format_crawl_error`, upgraded ping
- **01-03**: README, server registration in `~/.claude.json`, end-to-end verification

Phase 2 (crawl tools) can begin immediately. The `ctx.request_context.lifespan_context.crawler` pattern is established and the server is registered globally.

---
*Phase: 01-foundation*
*Completed: 2026-02-20*
