# Refresh stale docs: application-flow, CHANGELOG decision, CLAUDE.md drift

## Review context

- **Source:** deep-dive documentation review, 2026-07-06.
- **Confidence:** CONFIRMED (each stale claim verified against code).
- **Severity:** MEDIUM–HIGH within the docs dimension: two docs are
  substantively wrong/abandoned; the rest are point fixes.
- **Category:** documentation.

## Goal

Fix the user-facing documentation drift found by the review: the two
stale/abandoned docs (`docs/application-flow.md`, `CHANGELOG.md`) and the
point inaccuracies in otherwise-current docs.

## Problem (verified 2026-07-06)

- **`docs/application-flow.md` documents 3 of 5 CLI modes** ("The script
  has three top-level modes", :4-8) — it predates `serve` (PR #136,
  landed five hours after the doc's last commit) and `trace-bundle`
  (PR #140). Its entry-point claim (:3, "main() in
  anomaly-metric-creator.py") is also wrong — dispatch lives at
  [legacy.py:8770](src/anomaly_metric_creator/legacy.py:8770)-8779. The
  doc omits server mode, continuous generation, MCP/eval, and atomic
  publication entirely.
- **`CHANGELOG.md` is abandoned:** last entry 2026-06-24; ~64 PRs merged
  since (MCP, eval mode, atomic writes, hardening, the entire
  decomposition); `version = "0.3.0"` in pyproject.toml; the live
  "## Unreleased" heading advertises maintenance that is not happening.
- **CLAUDE.md "Changing time range"** (:1969-1973) says edit `START` in
  source — `--start-time` has existed since PR #138
  ([legacy.py:7807](src/anomaly_metric_creator/legacy.py:7807);
  README.md:271); CLAUDE.md never mentions the flag. Also the preamble
  facade sentence ("Most still re-export through `legacy.py`") is now 2
  of 5.
- **AGENTS.md:50** claims legacy.py "~12,800 lines" (9,188 as of
  2026-07-06); its key-files table predates all ten extractions.
- **docs/topology.md:3** says `TOPOLOGY` lives in
  `anomaly-metric-creator.py` (it is
  [legacy.py:3076](src/anomaly_metric_creator/legacy.py:3076)); all other
  topology.md facts verified accurate.
- **docs/repomix-map.md** lists the archived `07-02-decomp-otel-stream`
  task as active — one `scripts/update_repomix` run fixes it.
- Minor: `.trellis/spec/amc/backend/api-cli-server.md` omits the
  `--emit gauges` → `metrics` dependency (goes with the spec-backfill
  task if that lands first).

## Requirements

- Rewrite `docs/application-flow.md`: five-mode dispatch (`generate`,
  `combine`, `validate`, `serve`, `trace-bundle`), a serve-lifecycle lane
  (generate-once → SimulationState → MCP/eval → continuous-generate +
  atomic publication), corrected entry-point path.
- **CHANGELOG decision (explicit):** either backfill a 0.4.0-shaped entry
  covering the serve/MCP/eval/atomic/hardening era and wire changelog
  updates into the finish-work flow, or replace the file with a short
  "see git log / releases" pointer. A dangling "Unreleased" section is
  the worst state. Align the pyproject `version` with the chosen story.
- CLAUDE.md: replace the edit-`START` guidance with `--start-time`; fix
  the facade summary sentence.
- AGENTS.md: drop hardcoded line counts (they auto-stale during an active
  decomposition); regenerate the key-files table or point at the repomix
  map.
- Fix the topology.md path claim; run `scripts/update_repomix`.
- *(Added 2026-07-07, review-ledger completion)* README: document the
  atomic-publication contract in the output-files section — artifacts are
  staged as `<name>.tmp` siblings and `os.replace`d into place, so
  concurrent readers (notably `amc serve` under `--continuous-generate`)
  never observe partial files, and a crashed run may leave `*.tmp`
  siblings that the next run sweeps. README's only current "atomic" hit
  is Helm's `--atomic` flag (README.md:522); the contract itself is
  documented in CLAUDE.md and `tests/test_atomic_writes.py` but invisible
  to README-only users.

## Acceptance Criteria

- [x] application-flow.md names all five modes and passes a fact
      spot-check against `_SUBCOMMANDS` and the serve lifecycle.
- [x] CHANGELOG state is deliberate (backfilled or retired) and the
      pyproject version matches it.
- [x] CLAUDE.md contains `--start-time` guidance, no edit-`START`
      instruction, and an accurate facade sentence.
- [x] No doc claims `TOPOLOGY`/`main()` live in the shim file.
- [x] README describes the atomic-publication contract (`.tmp` staging +
      replace; serve-mode readers never see partial artifacts).
- [x] repomix map regenerated.

## Resolution (2026-07-07)

- **application-flow.md** rewritten: intro now names all five modes with the
  corrected entry-point path (`legacy.py`, not the shim); the mermaid
  dispatch node gains `serve` and `trace-bundle` branches; a serve-lifecycle
  note (generate-once → SimulationState → MCP/eval → continuous-generate +
  atomic publication) and an atomic-writes note added. The accurate
  generate-pipeline detail was preserved untouched.
- **CHANGELOG decision:** backfilled the `Unreleased` section to accurately
  cover the whole serve/MCP/eval/`trace-bundle`/atomic/hardening/`--start-time`/
  SECURITY.md/py3.14 era plus an Internal note on the decomposition — fixing
  the "dangling with one item" state. Added a maintenance-policy header
  (Unreleased = merged-but-untagged; git history authoritative between
  releases). No pyproject version bump: no release was cut, so these are
  correctly Unreleased.
- **CLAUDE.md:** `--start-time` guidance replaces the edit-`START`
  instruction; the facade summary sentence corrected to "three re-export from
  extracted impls; `models.py`/`scenarios.py` still route through legacy."
- **AGENTS.md:** dropped the hardcoded "~12,800 lines" (points at
  `docs/repomix-map.md` for current sizes).
- **topology.md:** `TOPOLOGY` path corrected to `legacy.py`.
- **README:** atomic-publication contract documented as a lead-in to the
  Output files section.
- **repomix map** regenerated via `scripts/update_repomix`.

Verified: role-name lint clean on all touched docs; the five-mode dispatch
and serve lifecycle present; no `three top-level modes` / `~12,800` /
`Most still re-export` stale strings remain.

## Notes

- README "Significant changes" list (:29-77) is frozen at the 0.2/0.3 era
  — refresh it here if the CHANGELOG decision is "retire" (the README
  list becomes the only recency surface).
- CLAUDE.md diet (shrinking per-feature sections to spec pointers) is a
  candidate follow-on once `07-06-trellis-spec-server-era-backfill`
  lands; out of scope here.
