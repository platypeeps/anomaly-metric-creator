# Implementation plan — debug UI CSV formula neutralization

Order matters: the guard lands before the lint that pins it, so the lint's
first run is against a correct pair rather than a known-drifted one.

## Step 1 — the guard

- [x] `src/anomaly_metric_creator/server_debug_ui.py` — rewrite `csvCell`
      (currently line 560) per design D1: neutralize first, then quote. Carry
      the marker comment naming `trace_bundle._CSV_FORMULA_TRIGGERS` and
      `tools/check_csv_formula_trigger_lockstep.py` on the guard line — the
      lint anchors on it.
- [x] `src/anomaly_metric_creator/trace_bundle.py` — extend the
      `_CSV_FORMULA_TRIGGERS` comment (line 25-27) with the reciprocal
      cross-reference to the debug UI's `csvCell`.

Validate: `.venv/bin/pytest tests/test_debug_ui_javascript.py`

## Step 2 — module-size ceiling

- [x] `tools/check_module_size.py` — raise the `RATCHET` ceiling for
      `server_debug_ui.py` from 1189 to the file's new line count.

Validate: `.venv/bin/python tools/check_module_size.py`

## Step 3 — the lockstep lint

- [x] `tools/check_csv_formula_trigger_lockstep.py` — new guard per design D2.
      Module docstring carries the full contract (both sites, both extraction
      strategies, the `0`/`1`/`2` exit split), because CLAUDE.md's repository-
      lints table points reviewers at the script rather than at a copy of it.
      Optional path arguments default to the repo-root files.
- [x] `tests/test_csv_formula_trigger_lockstep_lint.py` — new. Cover: in-step
      pair exits `0`; a fixture pair with a dropped trigger exits `1`; a
      fixture missing the Python literal exits `2`; a fixture missing the JS
      marker exits `2`. Fixtures under `tmp_path`, passed as path arguments.

Validate: `.venv/bin/python tools/check_csv_formula_trigger_lockstep.py` and
`.venv/bin/pytest tests/test_csv_formula_trigger_lockstep_lint.py`

## Step 4 — CI and pre-commit wiring

- [x] `.pre-commit-config.yaml` — add hook `csv-formula-trigger-lockstep`
      modeled on `trace-payload-antipatterns` (line 226), with
      `files: ^src/anomaly_metric_creator/(trace_bundle|server_debug_ui)\.py$`
      and `pass_filenames: false` (the guard reads both files as a pair, so it
      must not be handed a one-file subset).
- [x] `.github/workflows/ci.yml` — add
      `tests/test_csv_formula_trigger_lockstep_lint.py` to the quick-lane test
      list (the operand block at lines 365-383). The comment above that list
      (lines 344-361) is explicit that *every*
      lint's test file is listed, not only the ones lane selection requires.

Validate: `.venv/bin/python tools/check_guard_ci_coverage.py --list` must show
the new lint with `needs=QUICK+FULL has=QUICK+FULL`, and the
"lints whose own tests never run in the QUICK lane" section must still print
`none`.

## Step 5 — the tests for the guard itself

- [x] `tests/test_debug_ui_javascript.py` — add the two tests from design D3:
      the node-driven behavioral test (skipping when `shutil.which("node")` is
      `None`, matching the existing test) and the node-independent assertion
      over `DEBUG_HTML`.

Validate: `.venv/bin/pytest tests/test_debug_ui_javascript.py -v`

## Step 6 — docs, spec, and ledger

The blast radius was enumerated with `git grep`, not guessed. Four live sites;
the archived `07-17-audit-trace-export-hardening` artifacts and
`.trellis/workspace/sdelmas/journal-2.md:1092` are historical records of what
was true then and are deliberately **not** edited.

- [x] `.trellis/spec/amc/backend/api-cli-server.md:469-475` — the durable rule
      changes, so per CLAUDE.md the focused spec is updated *first*. The
      paragraph currently scopes the guard to `write_trace_bundle_csv`; extend
      it to name the debug UI's `csvCell` as the second enforcement point and
      the lockstep lint as what holds the trigger sets together. Add
      `src/anomaly_metric_creator/server_debug_ui.py` and
      `tools/check_csv_formula_trigger_lockstep.py` to its Sources list.
- [x] `SECURITY.md:151` — drop "Note that the debug UI's own client-side CSV
      download does not yet carry this guard." and state that both the writer
      and the debug UI download carry it, pinned by the lint.
- [x] `.trellis/audit/ledger.md:203` — A-018's `follow-up:` line asserts the
      debug UI "carries no equivalent guard", which this task falsifies. Update
      it to record the follow-up as landed, per the ledger's own convention for
      a closed follow-up. Leave `status: fixed` and the A-018 evidence block
      alone — they describe the writer-side fix and stay true.
- [x] `CLAUDE.md` — add a row for the new guard to the Repository lints table.
      That table is the reviewer-facing inventory; a lint missing from it is
      the exact drift the table exists to prevent.

Validate: `git grep -n "does not yet carry this guard"` must return no hits
outside this task's own planning artifacts (`git grep` skips `.venv/`; a bare
`grep -rn .` would descend into it). Also `git grep -in "client-side CSV"` to
catch a paraphrase of the same caveat.

## Step 7 — full gates

- [x] `.venv/bin/pytest`
- [x] `.venv/bin/pre-commit run --all-files`
- [x] `~/.agents/bin/sd-ai-command-pack-full-check.sh`

## Review gates

- Design D1's ordering (neutralize before quote) is the one correctness claim a
  reviewer should re-derive from the emitted bytes, not from the diff. The
  node test in step 5 asserts `"'=a,b"` explicitly for exactly that reason.
- Step 2's ceiling bump is a reviewed line by construction — flag it in the PR
  body so it is not read as ratchet erosion.

## Rollback points

Each step is independently revertible. Steps 3-4 (the lint) can be dropped
without affecting the guard if CI wiring turns out to be contentious; the
cross-reference comments from step 1 then become the PRD's stated minimum.
