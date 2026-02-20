---
phase: 03-profile-system
plan: "01"
subsystem: profiles
tags: [tdd, profiles, config, merge-engine]
dependency_graph:
  requires: []
  provides: [ProfileManager, build_run_config, PROFILES_DIR, KNOWN_KEYS]
  affects: [server.py, crawl_url]
tech_stack:
  added: [PyYAML (safe_load via crawl4ai transitive dep)]
  patterns: [TDD (red/green), flat YAML profile dicts, three-layer dict merge]
key_files:
  created:
    - src/crawl4ai_mcp/profiles.py
    - tests/__init__.py
    - tests/test_profiles.py
  modified: []
decisions:
  - "KNOWN_KEYS as frozenset excludes verbose — verbose always forced after merge"
  - "word_count_threshold popped from merged dict and routed to PruningContentFilter"
  - "Per-call-only keys (css_selector, wait_for, etc.) tracked separately in _PER_CALL_KEYS to avoid spurious unknown-key warnings"
  - "ProfileManager.get(name) returns dict copy — never exposes internal state"
metrics:
  duration: "2 minutes"
  completed: "2026-02-20"
  tasks_completed: 2
  files_changed: 3
---

# Phase 3 Plan 01: ProfileManager + build_run_config Summary

ProfileManager loads YAML profiles from a profiles/ directory at startup and build_run_config merges them with a guaranteed verbose=False for safe MCP transport.

## What Was Built

Two pure Python units that form the core of the Phase 3 profile system:

**`src/crawl4ai_mcp/profiles.py`** exports:
- `ProfileManager` — scans `*.yaml` files in a directory at `__init__` time, stores them as plain dicts; malformed files are logged and skipped (never crashes)
- `build_run_config(profile_manager, profile, **per_call_overrides)` — three-layer merge `default <- named <- per-call`, strips unknown keys, forces `verbose=False`, pops `word_count_threshold` into `PruningContentFilter`, returns `CrawlerRunConfig`
- `PROFILES_DIR` — `Path(__file__).parent / "profiles"` (default location for YAML files)
- `KNOWN_KEYS` — frozenset of valid YAML-settable CrawlerRunConfig kwargs

**`tests/test_profiles.py`** — 33 test cases across 7 test classes covering all specified behaviors.

## TDD Flow

**RED phase (commit bb8aad5):** 33 test cases written first. All failed with `ModuleNotFoundError` — `profiles.py` did not exist.

**GREEN phase (commit fec4c16):** `profiles.py` implemented. All 33 tests pass. Lint clean.

## Key Design Decisions

1. **`KNOWN_KEYS` excludes `verbose`** — if a profile YAML tries to set `verbose`, it gets stripped with an unknown-key warning. The `verbose=False` override is applied unconditionally after all merging, making it impossible for any profile or per-call override to enable verbose output (which would corrupt MCP transport).

2. **`_PER_CALL_KEYS` separation** — per-call params like `css_selector`, `wait_for`, `js_code`, `user_agent` are valid `CrawlerRunConfig` kwargs but not valid YAML profile keys. They're tracked in `_PER_CALL_KEYS` so they pass through per-call overrides without triggering spurious unknown-key warnings.

3. **`word_count_threshold` routing** — this field is in `KNOWN_KEYS` (valid in YAML profiles) but is explicitly `pop()`ed from the merged dict before `CrawlerRunConfig(**merged)`. It goes instead to `PruningContentFilter(min_word_threshold=wct)`. If not set anywhere, defaults to 10 (matching current `_build_run_config` behavior, preserving Phase 2 behavior).

4. **Flat profiles only** — profiles are shallow dicts. No nested YAML structures. `{**a, **b, **c}` merge is correct because all values are scalars/booleans.

5. **ProfileManager.get() returns copies** — mutating the returned dict cannot affect internal `_profiles` state.

## Verification

```
uv run pytest tests/test_profiles.py -v    # 33 passed in 0.59s
uv run ruff check src/                      # All checks passed!
uv run python -c "from crawl4ai_mcp.profiles import ProfileManager, build_run_config, PROFILES_DIR, KNOWN_KEYS; print('ok')"
# ok
```

## Deviations from Plan

None — plan executed exactly as written.

The implementation closely follows the patterns in `03-RESEARCH.md`. One small addition not explicitly in the plan: `_PER_CALL_KEYS` frozenset to separate per-call params from YAML-profile keys, preventing spurious unknown-key warnings on valid per-call overrides. This is Rule 2 (missing critical functionality) — without it, every `build_run_config` call with `css_selector` would log a spurious warning.

## Self-Check

- [x] `src/crawl4ai_mcp/profiles.py` exists
- [x] `tests/test_profiles.py` exists (33 tests, > 60 min_lines threshold)
- [x] All 33 tests pass
- [x] Lint clean (ruff check src/)
- [x] Import sanity: `ProfileManager, build_run_config, PROFILES_DIR, KNOWN_KEYS` all importable
- [x] Commits bb8aad5 (RED) and fec4c16 (GREEN) exist

## Self-Check: PASSED
