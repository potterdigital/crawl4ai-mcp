# Phase 8: Verify Profile System & Close Traceability - Research

**Researched:** 2026-02-22
**Domain:** Verification process, traceability audit, REQUIREMENTS.md maintenance
**Confidence:** HIGH (all findings based on direct codebase inspection and existing project artifacts)

## Summary

Phase 8 is a **process/documentation phase**, not a code phase. All code for the Profile System was completed in Phase 3 (2026-02-20) and has been running in production, consumed by 5 downstream tools (crawl_url, create_session, crawl_many, deep_crawl, crawl_sitemap). The sole gap is that Phase 3 never received a formal VERIFICATION.md file, causing the v1.0 milestone audit to classify PROF-01 through PROF-04 as "orphaned" despite the code being present, tested (33 unit tests), and human-verified via live MCP transport.

Phase 8 requires exactly two deliverables: (1) a `03-VERIFICATION.md` file in the Phase 3 directory that formally verifies PROF-01 through PROF-04 against the existing code and tests, and (2) an update to `REQUIREMENTS.md` to flip the 4 PROF requirement checkboxes from `[ ]` to `[x]` and change their status from `Pending` to `Complete`. The milestone audit should then produce `passed` with 28/28 requirements.

**Primary recommendation:** Follow the exact VERIFICATION.md format established by Phases 1, 2, 4, 5, 6, and 7. Map each PROF requirement to specific code locations, test coverage, and SUMMARY.md evidence. Update REQUIREMENTS.md traceability. Re-run the audit methodology to confirm 28/28.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| PROF-01 | Server ships with three built-in profiles: `fast`, `js-heavy`, `stealth` | Code evidence: 4 YAML files exist at `src/crawl4ai_mcp/profiles/{default,fast,js_heavy,stealth}.yaml`. ProfileManager loads them at startup. 03-02-SUMMARY.md claims completion. 33 tests pass. |
| PROF-02 | User can view all profiles and config via `list_profiles` MCP tool | Code evidence: `list_profiles` tool at server.py:379-412, registered with `@mcp.tool()`. Accesses `app.profile_manager.all()`. 03-03-SUMMARY.md claims completion. |
| PROF-03 | User can add/edit custom profiles via YAML files in `profiles/` dir without code changes | Code evidence: `ProfileManager._load_all()` uses `Path.glob("*.yaml")` to auto-discover profiles. `list_profiles` docstring documents: "create a YAML file in the profiles/ directory". 03-03-SUMMARY.md claims completion. |
| PROF-04 | Any crawl tool call can specify a profile name with per-call overrides (merge order: default -> profile -> per-call) | Code evidence: `build_run_config()` at profiles.py:125-187 implements three-layer merge. `crawl_url` has `profile: str | None = None` parameter. 5 tools call `build_run_config`. 03-02-SUMMARY.md claims completion. |
</phase_requirements>

## Standard Stack

### Core

This is a verification/documentation phase. No new libraries or code changes are needed.

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| N/A | N/A | No code changes required | Phase 8 produces documentation only (VERIFICATION.md + REQUIREMENTS.md updates) |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| N/A | N/A | N/A | N/A |

### Alternatives Considered

None. This is a documentation-only phase.

## Architecture Patterns

### Pattern 1: VERIFICATION.md Format (Established Project Convention)

**What:** A structured verification report with YAML frontmatter, Observable Truths table, Required Artifacts table, Key Link Verification, Requirements Coverage, Anti-Patterns scan, and optional Human Verification section.

**When to use:** Every phase must have a VERIFICATION.md before the milestone audit considers it verified.

**Format (from existing Phases 1, 2, 4-7):**

```markdown
---
phase: 03-profile-system
verified: [ISO 8601 timestamp]
status: passed
score: N/N must-haves verified
re_verification: false
---

# Phase 3: Profile System Verification Report

## Goal Achievement
### Observable Truths
| # | Truth | Status | Evidence |
...

### Required Artifacts
| Artifact | Expected | Status | Details |
...

### Key Link Verification
| From | To | Via | Status | Details |
...

### Requirements Coverage
| Requirement | Source Plan | Description | Status | Evidence |
...

### Anti-Patterns Found
| File | Pattern | Severity | Impact |
...

### Human Verification Required (if any)
...
```

**Key formatting rules observed from existing VERIFICATION.md files:**
- Frontmatter MUST include `phase`, `verified`, `status`, `score`
- Observable Truths map to the phase's Success Criteria from ROADMAP.md (Phase 3 has 5 success criteria)
- Evidence must cite specific line numbers, function names, file paths, or test names
- Requirements Coverage maps each requirement ID (PROF-01..04) to its source plan and evidence
- Anti-Patterns section scans for TODO/FIXME/print()/placeholder code
- Score format is `N/N must-haves verified`

### Pattern 2: REQUIREMENTS.md Traceability Update

**What:** The traceability table in REQUIREMENTS.md tracks each requirement's checkbox status and mapped phase. Currently PROF-01 through PROF-04 show `[ ]` (unchecked) and status "Pending". They need to be changed to `[x]` and "Complete".

**Current state of traceability table (from REQUIREMENTS.md lines 111-114):**
```markdown
| PROF-01 | Phase 3 | Pending |
| PROF-02 | Phase 3 | Pending |
| PROF-03 | Phase 3 | Pending |
| PROF-04 | Phase 3 | Pending |
```

**Target state:**
```markdown
| PROF-01 | Phase 3 | Complete |
| PROF-02 | Phase 3 | Complete |
| PROF-03 | Phase 3 | Complete |
| PROF-04 | Phase 3 | Complete |
```

Also, the requirement definitions themselves (lines 48-51) need checkbox updates from `- [ ]` to `- [x]`.

**Coverage summary update needed:** Change from "Complete: 24" / "Pending (orphaned): 4" to "Complete: 28" / "Pending: 0".

### Pattern 3: Milestone Audit Re-verification

**What:** After creating VERIFICATION.md and updating REQUIREMENTS.md, the milestone audit methodology should produce `passed` with 28/28 requirements.

**Audit methodology (from the existing audit report):**
1. Each requirement is checked against 3 sources: **V** (VERIFICATION.md), **S** (SUMMARY.md), **T** (REQUIREMENTS.md traceability checkbox)
2. A requirement is `satisfied` when it has at least V=passed
3. A requirement is `orphaned` when V=missing
4. The milestone passes when all requirements have V=passed

**After Phase 8:** PROF-01..04 will have V=passed, S=listed, T=[x] -- satisfying all three sources.

### Anti-Patterns to Avoid

- **Fabricating evidence:** Every line number, function name, and test name cited in VERIFICATION.md must be verifiable against the current codebase. Do not cite outdated line numbers.
- **Skipping the full format:** Do not write a minimal VERIFICATION.md. Follow the established format from Phases 1, 2, 4-7 exactly.
- **Forgetting to update both locations in REQUIREMENTS.md:** The checkboxes appear in TWO places -- the requirement definitions (lines 48-51) AND the traceability table (lines 111-114). Both must be updated.
- **Incorrect coverage count:** After updating, verify the arithmetic: 28 total, 28 complete, 0 pending.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Verification format | New custom format | Existing VERIFICATION.md pattern from Phases 1-7 | Consistency with established project convention; audit tooling expects this format |
| Evidence gathering | Manual line counting | Serena `find_symbol` with `include_body=True` | Precise line numbers from LSP, not guessing |

**Key insight:** This phase is 100% documentation. There is no code to write. The only risk is inaccuracy in the verification evidence.

## Common Pitfalls

### Pitfall 1: Stale Line Numbers

**What goes wrong:** VERIFICATION.md cites line numbers for functions/tools, but the code has been modified since Phase 3 (Phases 4-7 added tools, shifting line numbers).
**Why it happens:** The code file (server.py) grew from ~200 lines to ~1300 lines between Phase 3 and Phase 7.
**How to avoid:** Use `find_symbol` or `grep` to get current line numbers at verification time, not from SUMMARY.md artifacts which reference the state at Phase 3 completion.
**Warning signs:** Line numbers cited for Phase 3 artifacts that don't match current file content.

### Pitfall 2: REQUIREMENTS.md Partial Update

**What goes wrong:** Updating the traceability table but forgetting the requirement definition checkboxes (or vice versa), leaving the file internally inconsistent.
**Why it happens:** Requirements appear in two sections of REQUIREMENTS.md.
**How to avoid:** Search for all occurrences of "PROF-01" through "PROF-04" in REQUIREMENTS.md and update every instance.
**Warning signs:** The coverage summary says 28/28 but `grep '\[ \]' REQUIREMENTS.md` still returns matches.

### Pitfall 3: Missing the "three profiles" vs "four YAML files" Distinction

**What goes wrong:** PROF-01 says "three built-in crawl profiles: fast, js-heavy, stealth" but there are actually 4 YAML files (including `default.yaml`). The verifier might flag this as a discrepancy.
**Why it happens:** `default.yaml` is not a user-selectable "profile" in the PROF-01 sense -- it's the base layer always applied. The three named profiles are fast, js_heavy, and stealth.
**How to avoid:** Explicitly note in the verification that `default.yaml` is the base layer (not one of the "three built-in profiles") and that the three profiles matching PROF-01 are fast, js_heavy, and stealth.

### Pitfall 4: Not Verifying Downstream Profile Consumption

**What goes wrong:** VERIFICATION.md confirms profiles.py exists and has tests, but does not verify that the profile system is actually consumed by downstream tools.
**Why it happens:** Phase 3's scope was just the profile system, but the evidence is stronger if downstream usage is also documented.
**How to avoid:** Note that `build_run_config` is called by 5 tools: crawl_url, create_session, crawl_many, deep_crawl, crawl_sitemap. This strengthens the PROF-04 evidence.

## Code Examples

No code to write. The key artifacts to verify already exist:

### Profile System Code Locations (Current)

```
src/crawl4ai_mcp/profiles.py         -- ProfileManager class, build_run_config function
src/crawl4ai_mcp/profiles/default.yaml -- Base layer profile
src/crawl4ai_mcp/profiles/fast.yaml    -- Fast profile (PROF-01)
src/crawl4ai_mcp/profiles/js_heavy.yaml -- JS-heavy profile (PROF-01)
src/crawl4ai_mcp/profiles/stealth.yaml  -- Stealth profile (PROF-01)
src/crawl4ai_mcp/server.py            -- list_profiles tool (PROF-02), crawl_url profile param (PROF-04)
tests/test_profiles.py                -- 33 unit tests covering merge, verbose enforcement, unknown keys
```

### Requirement-to-Code Mapping

| Requirement | Primary Evidence | Supporting Evidence |
|-------------|-----------------|---------------------|
| PROF-01 | `profiles/{fast,js_heavy,stealth}.yaml` exist with correct content | `ProfileManager._load_all()` loads them; 03-02-SUMMARY.md confirms |
| PROF-02 | `list_profiles` tool at server.py (registered `@mcp.tool()`) | Returns formatted markdown of all profiles; 03-03-SUMMARY.md confirms human-verified via live MCP |
| PROF-03 | `ProfileManager._load_all()` uses `Path.glob("*.yaml")` | `list_profiles` docstring documents the process; 03-03-SUMMARY.md confirms custom YAML discovery tested |
| PROF-04 | `build_run_config()` three-layer merge at profiles.py | `crawl_url` has `profile` param; 5 tools call `build_run_config`; 33 tests cover merge logic |

### Observable Truths for VERIFICATION.md (mapped from ROADMAP.md Phase 3 Success Criteria)

The Phase 3 success criteria from ROADMAP.md define exactly 5 observable truths:

1. Calling crawl tool with `profile="fast"` applies fast profile config
2. Calling crawl tool with `profile="stealth"` applies stealth profile config
3. Per-call parameter override takes precedence over profile value (merge order: default -> profile -> per-call)
4. Claude can call `list_profiles` and see all available profiles with full config
5. Adding a new YAML file to `profiles/` makes it available without code changes

## State of the Art

| Old Approach | Current Approach | Impact |
|--------------|------------------|--------|
| No VERIFICATION.md for Phase 3 | Create 03-VERIFICATION.md following established format | Closes all 4 orphaned requirements |
| REQUIREMENTS.md shows 24/28 complete | Update to 28/28 complete | Milestone audit passes |
| v1.0 audit status: gaps_found | Re-audit: passed | Clean milestone closure |

## Open Questions

1. **Should VERIFICATION.md be placed in the Phase 3 directory or the Phase 8 directory?**
   - What we know: The audit expects `03-VERIFICATION.md` in the Phase 3 directory (`.planning/phases/03-profile-system/`). Other phases' verification files are in their own phase directories.
   - Recommendation: Place it at `.planning/phases/03-profile-system/03-VERIFICATION.md` since it verifies Phase 3. Phase 8's deliverable IS the Phase 3 verification file. Phase 8 may optionally have its own summary/completion artifact, but the VERIFICATION.md belongs to Phase 3.

2. **Does the milestone audit need to be formally re-run?**
   - What we know: The success criteria say "Re-running the milestone audit produces `passed` status with 28/28 requirements." The existing audit file at `.planning/v1.0-MILESTONE-AUDIT.md` was produced by an orchestrator agent.
   - Recommendation: Either re-run the audit via the same orchestrator, or manually update the frontmatter status and scores in the existing audit file to reflect the gap closure. Updating the existing file is simpler and equally valid since the gap was documented precisely.

3. **Should Phase 8 itself have a VERIFICATION.md?**
   - What we know: Phase 8 is a gap-closure phase. Its deliverables are the Phase 3 VERIFICATION.md and the REQUIREMENTS.md update. There is no code to verify.
   - Recommendation: Phase 8 does not need its own VERIFICATION.md. The success criteria are met by the existence of the Phase 3 VERIFICATION.md and the updated REQUIREMENTS.md. A SUMMARY.md for Phase 8 documenting what was done would be sufficient.

## Sources

### Primary (HIGH confidence)

- Direct codebase inspection via Serena:
  - `profiles.py` symbols: `ProfileManager`, `build_run_config`, `KNOWN_KEYS`, `_PER_CALL_KEYS`
  - `server.py` symbols: `list_profiles` (lines 379-412), `crawl_url` with `profile` param (lines 461-596)
  - `build_run_config` referenced by 5 tools in server.py (crawl_url, create_session, crawl_many, deep_crawl, crawl_sitemap)
  - 4 YAML profile files confirmed at `src/crawl4ai_mcp/profiles/`
  - 33 tests in `tests/test_profiles.py`
  - 94 total tests passing

- Existing project artifacts:
  - `.planning/v1.0-MILESTONE-AUDIT.md` -- documents the exact gap (missing 03-VERIFICATION.md)
  - `.planning/REQUIREMENTS.md` -- current traceability state (PROF-01..04 marked Pending)
  - `.planning/ROADMAP.md` -- Phase 3 success criteria (5 items) and Phase 8 definition
  - `.planning/phases/03-profile-system/03-01-SUMMARY.md` -- claims PROF-01, PROF-04
  - `.planning/phases/03-profile-system/03-02-SUMMARY.md` -- claims PROF-01, PROF-04
  - `.planning/phases/03-profile-system/03-03-SUMMARY.md` -- claims PROF-02, PROF-03

- Existing VERIFICATION.md files (format reference):
  - Phase 1: `.planning/phases/01-foundation/01-VERIFICATION.md`
  - Phase 4: `.planning/phases/04-extraction/04-VERIFICATION.md`
  - Phase 7: `.planning/phases/07-update-management/07-VERIFICATION.md`

### Secondary (MEDIUM confidence)

None needed. All evidence is from direct codebase inspection.

### Tertiary (LOW confidence)

None.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- no new libraries or code, purely documentation
- Architecture: HIGH -- VERIFICATION.md format established by 6 existing examples in this project
- Pitfalls: HIGH -- pitfalls identified from direct comparison of current code state vs. Phase 3 artifacts

**Research date:** 2026-02-22
**Valid until:** 2026-06-22 (no external dependencies; validity tied to codebase stability)
