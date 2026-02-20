# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-19)

**Core value:** Claude Code can crawl any page, extract any content (markdown or structured JSON), and orchestrate deep multi-page crawls — all through MCP tool calls, without leaving the coding session.
**Current focus:** Phase 1 — Foundation

## Current Position

Phase: 1 of 7 (Foundation)
Plan: 3 of 3 in current phase (01-03 complete — Phase 1 DONE)
Status: Phase 1 complete, ready for Phase 2
Last activity: 2026-02-20 — Completed Plan 01-03: README, server registered in ~/.claude.json, Phase 1 complete

Progress: [███░░░░░░░] 14%

## Performance Metrics

**Velocity:**
- Total plans completed: 3
- Average duration: 2 minutes
- Total execution time: 0.10 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 01-foundation | 3/3 | 6 min | 2 min |

**Recent Trend:**
- Last 5 plans: 3 min, 2 min, 1 min
- Trend: fast execution

*Updated after each plan completion*

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- [Init]: Python MCP server via FastMCP with stdio transport (crawl4ai is Python-only)
- [Init]: AsyncWebCrawler singleton via FastMCP lifespan (prevents browser memory leaks)
- [Init]: LLM extraction as separate opt-in tool (prevents cost surprises)
- [Init]: Stderr-only logging enforced from day one (protects MCP transport)
- [01-01]: logging.basicConfig(stream=sys.stderr) placed before ALL library imports — non-negotiable transport hygiene
- [01-01]: mcp.run() called bare (no asyncio.run() wrapper) to avoid double event loop error
- [01-01]: crawl4ai pinned to >=0.8.0,<0.9.0; resolved to 0.8.0
- [01-01]: mcp[cli] pinned to >=1.26.0; resolved to 1.26.0
- [Phase 01-foundation]: explicit crawler.start()/close() in try/finally (not async with) — lifespan IS the context manager
- [Phase 01-foundation]: BrowserConfig(verbose=False) mandatory — verbose=True corrupts MCP stdout transport
- [Phase 01-foundation]: _format_crawl_error returns structured string (not raises) so Claude can reason about failures
- [Phase 01-foundation]: --scope user confirmed as correct user-scoped registration flag in claude mcp add-json

### Pending Todos

None.

### Blockers/Concerns

- [Research]: Per-Chromium-tab memory on macOS with text_mode=True not precisely quantified — validate during Phase 5 with ps monitoring
- [Research]: MemoryAdaptiveDispatcher may be overly conservative on a dev machine with other apps; consider SemaphoreDispatcher(permits=3) as default
- [Research]: crawl4ai 0.8.0 deep crawl BFS API stability should be validated against live docs before Phase 5 implementation

## Session Continuity

Last session: 2026-02-20
Stopped at: Completed Plan 01-03 — README and MCP registration complete, Phase 1 Foundation DONE
Resume file: None
