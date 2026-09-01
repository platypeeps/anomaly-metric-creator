---
title: Move topology CSV helpers and lint run_tool boilerplate into conftest
status: planning
parked: 2026-09-01 bulk-park (D2)
created: 2026-08-06
---
# Move topology CSV helpers and lint run_tool boilerplate into conftest

## Goal

A-033 + A-037: conftest topology column helpers with SCENARIOS-derived exclusion
windows, and a shared `run_tool` subprocess helper for the lint tests.

Child 2 of epic `07-17-audit-test-harness-dedupe`. Both remaining children add
to `tests/conftest.py`; this one goes first and `08-06-otlp-capture-fixture`
rebases onto it — see **Sequencing** below.

## Measured baseline (main @ `29ee1bf`)

A-033 — topology harness:

| helper | copies | distinct bodies | files |
| --- | --- | --- | --- |
| `_column_values` | 4 | 2 | `test_topology_fanout.py:47`, `test_topology_llm.py:60`, `test_topology_saturation.py:51` (identical); `test_topology_loadbalancer_gateway.py:36` (diverged) |
| `_aligned_columns` | 3 | 2 | `test_topology_llm.py:70` + `test_topology_saturation.py:61` (identical); `test_topology_fanout.py:82` (diverged) |
| `_exclude_anomaly_rows` | 3 | 1 | fanout `:74`, llm `:148`, saturation `:95` — byte-identical |

Exclusion windows have already drifted, which is the finding's real cost:

- `test_topology_fanout.py:60` — 5 hand-written windows.
- `test_topology_saturation.py:77` — 7 hand-written windows.
- `test_topology_llm.py:145` — derived at import from `amc.SCENARIOS` via
  `_compute_llm_exclusion_windows()`, padded by `_EXCLUSION_PAD_SECONDS = 30`.

The derived version is LLM-specific: it hard-codes its interest set as
`apigateway.requests_per_sec` plus `_LLM_TOPOLOGY_AFFECTED_METRICS`
(`input_tokens_per_sec`, `avg_llm_latency_ms`, `p95_llm_latency_ms`,
`llm_api_error_rate`). Generalizing it means parameterizing that interest set
per caller, not lifting it verbatim.

A-037 — lint subprocess boilerplate:

- 20 `tests/*.py` files define a module-level `_run`. Only **16** are the lint
  shape (`subprocess.run` over a `tools/check_*.py`); the other 4
  (`test_instances_per_component.py`, `test_emit_selection_hygiene.py`,
  `test_instance_config.py`, `test_otel_gauges.py`) are `amc`/`out_dir`
  generation helpers and are **out of scope**.
- Among the lint 16, one body is repeated **8** times identically and a second
  body **2** times; the rest are near-variants that add `stdin=`, `env=`, a
  `repo_root` argument, or `Path` args.

## Requirements

1. Add the three topology helpers to `tests/conftest.py` as importable helpers
   (module-level functions, not fixtures — they take `out_dir` explicitly and
   have no per-test setup). Take the majority body for `_column_values` and
   `_aligned_columns`; reconcile the two diverged copies against it rather than
   adding a second variant.
2. Generalize the SCENARIOS-derived exclusion-window computation into one
   conftest helper that accepts the caller's interest set (component/metric
   pairs) and the pad, and returns sorted `(start, end)` string windows.
   `test_topology_llm.py` keeps its current behavior by passing its existing
   interest set.
3. Replace the hand-written `_EXCLUSION_WINDOWS` lists in
   `test_topology_fanout.py` and `test_topology_saturation.py` with calls to
   that helper. **Before deleting either list, prove the derived windows are a
   superset of the hand-written ones** — the hand lists carry per-scenario
   comments naming what each window covers, and a narrower derived set would
   silently re-admit anomaly rows into the correlation pools.
4. Add a `run_tool` helper to `tests/conftest.py` covering the lint shape, with
   optional `stdin`, `env`, and `cwd`/`repo_root`, returning
   `subprocess.CompletedProcess[str]`. Migrate all 16 lint tests onto it and
   delete their local `_run`. Leave the 4 generation-helper `_run` functions
   alone.
5. Decide and record in the PR whether the two contract checkers
   (`tools/check_copilot_instruction_contract.py`,
   `tools/check_ci_review_contract.py`) get a shared library for their
   identical `_read` / `_require_contains`, or stay documented-standalone.
   A-037's ledger fix sketch calls this optional; a decision either way closes
   it. Production `tools/` changes are in scope only if that decision is
   "shared library".
6. No production behavior change under `src/`. No golden-hash change.
7. `tests/conftest.py` heavy-fixture rules still apply: this task adds no
   GB-scale fixture, so neither `_HEAVY_SESSION_FIXTURES` nor
   `_HEAVY_MODULE_FIXTURES` should change. If one does, the change is out of
   scope.

## Sequencing

`08-06-otlp-capture-fixture` (child 3, A-032) is blocked on this task: it adds
`capture_otlp_server` to the same `tests/conftest.py`. The epic's
`implement.md` fixes this order — A-037, then A-033, then A-032 — so the
largest diff rebases onto a settled conftest instead of the reverse. This task
is not blocked on anything.

## Acceptance criteria

- [ ] `grep -rnE 'def (_column_values|_aligned_columns|_exclude_anomaly_rows)\(' tests/`
      returns **exactly three lines**, all in `tests/conftest.py` — one per
      helper. Confinement to `tests/conftest.py` alone is not enough: it would
      still pass with a helper defined twice in that file. (Use `-rnE`, not
      `-c` over a glob: a multi-file `grep -c` prints a per-file count line for
      every file, so it can never "return 0".) Pre-change this returns ten
      lines across four files.
- [ ] `grep -rn '_EXCLUSION_WINDOWS = \[' tests/` returns no hits — no
      hand-written window list remains.
- [ ] `grep -rnE 'def [a-z_]*exclusion_windows' tests/ | grep -v 'def test_'`
      returns **exactly one line**, and that line is in `tests/conftest.py` —
      the derivation itself exists once. The absence check above cannot
      establish this: it proves no *hand-written* list survives, not that the
      generalized computation was copied per caller, which would recreate A-033
      in a new shape. Confinement alone is not enough either — two derivations
      inside `conftest.py` would pass it. The `grep -v` drops test functions
      such as `test_anomaly_exclusion_windows_use_span_columns` in
      `test_validate_output.py`, which are unrelated. Pre-change this returns
      one line, `tests/test_topology_llm.py:116` — right count, wrong file.
- [ ] The superset proof from requirement 3 is recorded in the PR body: for
      each of the 5 fanout and 7 saturation hand-written windows, the derived
      window that covers it.
- [ ] Exactly 4 `tests/*.py` files still define a module-level `_run`, and all
      4 are the generation-helper shape listed above.
- [ ] The contract-checker decision (requirement 5) is stated in the PR body.
- [ ] `.venv/bin/pytest tests/test_topology_fanout.py tests/test_topology_llm.py tests/test_topology_saturation.py tests/test_topology_loadbalancer_gateway.py -n 0`
      passes, and the pass/fail set is unchanged from the pre-change run
      (capture both with `-q` and diff).
- [ ] `.venv/bin/pytest` full suite green; `.venv/bin/pre-commit run --all-files` clean.
- [ ] `git diff --stat src/` is empty.
- [ ] The same PR clears `08-06-otlp-capture-fixture`'s `blocked` / `blockedOn`
      markers in its `task.json` and appends an `UNBLOCKED <date>:` line to its
      `notes`, matching the convention `08-04-server-helm-impl-extract` used.
- [ ] A-033 and A-037 both read `status: fixed` in `.trellis/audit/ledger.md`
      in this PR (epic convention), each with a `last-seen` bump.

## Notes

- CLAUDE.md's test-hygiene guidance should name `run_tool` as the canonical
  lint-subprocess harness once it exists — the epic's `implement.md` lists this
  under documentation updates.
- Tests must stay order-independent and file-isolated for xdist; a conftest
  helper that caches across files would violate that.
