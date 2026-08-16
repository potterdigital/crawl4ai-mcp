# AGENTS.md

Context for AI coding agents working in this repository. `README.md` is for
people using the server; this file is for whatever is editing the code.

## What this is

A Python MCP server that wraps [crawl4ai](https://docs.crawl4ai.com) and
exposes web crawling as MCP tools. It runs as a `stdio` server, so it speaks
JSON-RPC over stdout to a client such as Claude Code, Cursor, or Zed.

It is a **wrapper**. Its job is to make crawl4ai pleasant to use from an MCP
client, never to reimplement it. Before adding hand-rolled logic, check whether
crawl4ai already does it, and read `docs/crawl4ai-boundary.md`, which records
what is hand-rolled here and the specific reason each piece exists.

## Setup and commands

```bash
uv sync                       # install (uv manages the virtualenv)
uv run crawl4ai-setup         # download the Chromium build Playwright needs
uv run pytest                 # run the test suite
uv run ruff check src/ tests/ # lint
uv run crawl4ai-doctor        # diagnose crawl4ai / Playwright health
uv run python -m crawl4ai_mcp.server        # run the server directly
uv run python -m crawl4ai_mcp.server 2>&1 1>/dev/null   # watch logs, discard MCP frames
```

Format only the files you change: `uv run ruff format <paths>`. Do not run it
across the repository; unrelated reformatting buries a real diff.

## Layout

- `src/crawl4ai_mcp/server.py` — every tool, the lifespan, browser recovery
- `src/crawl4ai_mcp/profiles.py` — `ProfileManager` and config merging
- `docs/crawl4ai-boundary.md` — what is ours vs crawl4ai's, and why
- `tests/` — pytest, no network access

## Invariants that will bite you

Each of these has a real incident behind it. Breaking one usually still passes
the test suite.

1. **stdout must stay clean.** The stdio transport uses stdout exclusively for
   JSON-RPC frames. Any stray byte corrupts the protocol and disconnects the
   client. So: no `print()` (ruff's `T201` rule catches it), `verbose=False` is
   forced on both configs, all logging goes to stderr, and any subprocess must
   have its output captured rather than inherited.

2. **Never create `AsyncWebCrawler` per tool call.** Chromium startup costs
   2-5 seconds. One instance is created in `app_lifespan` and reused. Reach it
   via `_require_crawler(app)`, never `app.crawler` directly — the helper
   raises an error naming the fix when the browser is unavailable.

3. **Never put per-call data on the shared crawler or its strategy.** The
   crawler is process-wide and crawl4ai keeps exactly one hook slot per hook
   type, so anything written there is visible to every concurrent call. This
   caused a real credential leak. Per-call headers and cookies go in the
   `_call_overrides` ContextVar; hooks are installed once at startup.
   `tests/test_credential_isolation.py` guards this.

4. **Tools never raise for an expected failure.** A bad URL, an unwritable
   directory, an invalid regex, a malformed sitemap: all of these are reported
   in the return value. Model-returning tools set `error`; string-returning
   tools return a structured error string. A partial result must survive, so
   one bad URL never discards a batch.

5. **Optional parameters that a profile may also set default to `None`.** A
   concrete default is indistinguishable from silence at the call site, so
   writing it into the config unconditionally means the profile can never win
   and silently breaks the documented merge order
   `default ← named profile ← per-call`.

6. **`CrawlResult.success` means "got a response", not HTTP 200.** A real 404
   arrives with `success=True` and the error page as content. Always carry and
   check `status_code`.

7. **Never exit the process on a recoverable browser fault.** Exiting before
   the transport opens means the client reports only "failed to connect" and
   the actionable message dies in a log nobody opens.
   `tests/test_browser_recovery.py` fails if that exit returns.

8. **Valid config keys come from `CrawlerRunConfig`'s live signature**, never a
   hand-maintained list. The same rule applies to provider names, which are
   read from litellm. A copied list drifts the moment upstream adds something.

## Testing

The suite mocks `CrawlResult`, which means **it cannot see whether a tool body
actually works**. A full-green run has repeatedly coexisted with tools that
crashed, ignored a parameter, or returned the wrong page count. Unit tests are
necessary and not sufficient.

For anything touching a tool body, drive the real server over stdio and check
the result against a real site or a local fixture server. Spawn the server as a
subprocess, send `initialize`, send `notifications/initialized`, then
`tools/call`. Parse every stdout line as JSON and treat any line that is not
JSON as a transport-corruption failure.

When writing tests:

- Name the failure mode each test guards, in the test or its docstring.
- Prefer a few tests with distinct failure modes over many permutations of one.
- Verify a new test by reintroducing the defect and watching it fail. A test
  that passes against the broken code is not a test.
- Do not assert on loose substrings. Match on something that can only mean the
  thing you are checking.
- Run a positive control before believing a negative result.

## Style

- Python 3.12+, type hints on public functions.
- Comments explain **why**, and cite the measurement or incident where there is
  one. This codebase deliberately carries longer comments than usual, because
  most of the non-obvious code exists to work around specific upstream
  behaviour and the next reader needs to know which.
- Keep the existing house voice in code comments and docs.

## Before you finish

- `uv run pytest` and `uv run ruff check src/ tests/` both clean.
- If you changed a tool body, exercise it against a real site.
- If you changed behaviour, update `CHANGELOG.md`, the tool docstring, and
  `README.md`. The docstrings are the API documentation an agent reads at call
  time, so a stale one is a defect.
- If you worked around upstream behaviour, record it in
  `docs/crawl4ai-boundary.md` with the evidence.

## Scope

Do not add paid dependencies. Do not commit secrets or `.env` files. `CLAUDE.md`,
`dev-docs/` and `.planning/` are gitignored and local-only; nothing internal
belongs in `docs/`, which is public.
