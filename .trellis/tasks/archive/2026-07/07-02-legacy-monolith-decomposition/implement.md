# Decompose legacy.py — Implementation Plan (remaining work, 2026-07-17)

Steps 1–7 are landed (PRs #178, #181, #183, #185, #198, #203); `legacy.py`
is ~9,188 lines. This plan covers only what remains. Every remaining step is
a child task with its own `design.md` / `implement.md`; this file fixes the
cross-child order, the shared per-PR protocol, and the epic-close checklist.

## Execution Order

1. **`07-06-validate-impl-split-and-cleanup`** — independent of steps 8–10
   (validate_impl.py is already extracted); smallest remaining item; restores
   the epic's own <800-line invariant and folds audit items A-011/A-068. Can
   land any time, but doing it first clears the recorded invariant breach
   before the epic takes on new extractions.
2. **Step 8 — `07-02-decomp-cli-args`** with the callback seam
   (`_configure_cli_runtime`), per design.md's 2026-07-17 Decision 1. Most
   contract-dense step: two-tier help, `p.set_defaults` env seeding,
   reconciliation-before-validation ordering all must survive byte-identical.
3. **Step 9A — `07-02-decomp-catalog-data`** — the already-landed
   `catalog.py` + `models_impl.py` extraction, including component/instance
   registries and their validators. The archived task's scenario half did not
   land and is owned by 9B below.
4. **Step 9B recovery — `07-21-decomp-scenario-catalog-recovery`** — finish
   the scenario builders/data/validation/resolution half that the archived
   step-9 task left undone; preserve live monkeypatch bindings and the sole
   import-time validation call.
5. **Step 10 — `07-02-decomp-generation-topology`** — the RNG-order-critical
   core; only after steps 8, 9A, and the recovered 9B have landed with
   unchanged hashes.
6. **Dispatch-root split — `07-21-decomp-legacy-dispatch-root`** — implement
   the maintainer-selected no-waiver path: extract run orchestration, preserve
   live legacy bindings, and bring `legacy.py` below 800 lines.
7. **Epic close** — after that child merges with unchanged hashes, update
   CLAUDE.md's final module map, flip remaining checklist boxes, archive.

Completed 2026-07-21: PR #291 supplied the final no-waiver dispatch-root split;
the parent and final child are ready for normal Trellis archive/session
bookkeeping.

## Per-PR Protocol (every remaining extraction)

- One extraction per PR; branch from `main`; PR opens as **draft** and walks
  the 14-heading pre-PR checklist before ready.
- Verbatim moves only: whole functions, unchanged bodies; `legacy.py` gets
  the re-import block at the same conceptual location with `as`-aliases.
- One-way imports: new modules never import `legacy`; live-registry needs go
  through a callback seam configured by `legacy.py` (schema_impl precedent).
- After the move, grep the deleted line range for `^from \.` re-imports —
  the step-5 splice hazard (a cut range swallowing a prior step's re-import
  stub) is documented in CLAUDE.md; confirm every leaf re-import resolves.
- Monkeypatch audit before coding: grep `tests/` for `monkeypatch.setattr`
  on every symbol in the moved range; any patched symbol follows the
  move-with-callers rule or gets a seam (child designs pin the per-name
  choice).

## Validation Plan (every PR)

```bash
.venv/bin/pytest                      # full suite = all locked golden hashes
python anomaly-metric-creator.py --help
.venv/bin/python -c "import anomaly_metric_creator.cli"  # console-script path
.venv/bin/ruff check src/ tests/
.venv/bin/pre-commit run --all-files
```

- Apply the `full-ci` label so the PR runs the full matrix (heavy-serial +
  light-parallel partition).
- For steps 9–10 additionally: `pytest tests/test_package_facades.py`
  (identity assertions) and a targeted run of the monkeypatching test files
  named in the child designs before the full suite.

## Documentation And Spec Updates

- CLAUDE.md module map updated **in the same PR** as each extraction
  (epic acceptance criterion; drift here is the audit's top recurring
  finding).
- `.trellis/spec/amc/backend/index.md` conventions updated if an extraction
  changes a documented seam (e.g. the new `_configure_cli_runtime`).
- On epic close: record the completed Decision 2 no-waiver split in CLAUDE.md
  and the final line-count evidence in the epic artifacts.

## Review Notes

- The RNG constraint is the reviewer-sensitive area: steps 8–9 must not
  touch any RNG call path (they don't, structurally — CLI parsing and data
  registries draw nothing); step 10 is *entirely* RNG-critical and its PR
  description must say "verbatim move, zero behavior delta, hashes prove it".
- Copilot re-flags cumulative diffs — verify against HEAD before re-fixing.

## Follow-Ups

- `_EMIT_ARTIFACT_FILES` relabel/move out from under the `# Combine step`
  header — earmarked for step 9 (task.json notes).
- Coupling-loop dedupe between `_compose_topology_coupled_specs` and
  `_compute_topology_arrays_per_instance` — its own hash-guarded PR *after*
  step 10 settles (recorded in the generation-topology PRD).
- Flip audit ledger items A-011/A-068 to `fixed` when
  `07-06-validate-impl-split-and-cleanup` closes.
