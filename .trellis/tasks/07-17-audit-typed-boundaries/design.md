# Type the spec/config/server boundaries — Design (SD Work Designs, 2026-07-17)

## Overview

Seven items typing the system's central seams. The load-bearing design
question is **sequencing against the decomposition epic** — three items
touch code that decomp steps 8–10 move. Rule adopted here: type a seam
*in or immediately after* the decomp PR that gives the code its final
home; never type code that is about to move (double churn + verbatim-move
conflicts).

## Proposal — four PRs keyed to the epic timeline

### PR 1 — independent items (now; no decomp dependency)

- **A-008:** shared `signal_stream_config(args)` builder in
  `otel_stream.py`; `legacy.main` (legacy.py:9104-9126) and
  `server._run_otel_streams` (server.py:1716-1734) both consume it.
  Reconcile the two rituals' subtle divergence first — diff them
  line-by-line and confirm which behavior is canonical per README's env
  contract before unifying (a silent semantic pick is the risk).
- **A-009:** keyword-only leading params on `combine_logs_unified`
  (compat: positional callers in-tree are updated; the facade export
  keeps working — grep external-looking call shapes in tests first).
- **A-010:** public aliases + `__all__` in `artifacts.py`, `timeutil.py`,
  `csv_layout.py` (underscore names stay as compat bindings).
- **A-002 (first half):** `server_mcp`/`server_ops` import leaf-resident
  helpers directly from `timeutil`/`csv_layout`/`schema_impl` instead of
  through `state.legacy` — those homes are stable now. The typed
  Protocol for the *genuinely legacy* surface waits for PR 4.

### PR 2 — A-007 RunConfig (after decomp step 8, `cli_args.py`)

Frozen `RunConfig` dataclass built by `_reconcile_cli_surface` in its
new `cli_args.py` home; `serve_main`/`build_state` consume it, deleting
the ~15 `getattr(args, "seed", 42)`-style re-hardcoded defaults
(server.py:1659 et al.) — each replaced by the typed field whose default
*is* the `DEFAULT_*` constant. The argparse Namespace keeps flowing in
parallel during migration; RunConfig wraps it rather than replacing every
consumer at once (mechanical, hash-safe).

### PR 3 — A-005 AnomalySpec/CascadeSpec (with/after decomp step 9)

Frozen spec dataclasses in `scenarios_impl.py`'s landing;
`_validate_scenario_spec` becomes a pure parser returning them (its
in-place `instance_filter` normalization and the runtime-stamped
`_scenario_id`/`_severity`/`_is_cascade` keys become explicit fields).
Byte-identical output constraint: the parse happens at the same points
the validation happens today; RNG order untouched (specs carry the same
data, differently shaped). Golden hashes gate.

### PR 4 — A-006 dispatch opt-in + A-002 Protocol (after decomp step 10)

- **A-006:** explicit generator calling-convention opt-in (a
  `generator_args=` spec field or wrapper markers); the
  signature-introspection dispatch (legacy.py:2121-2283 today; moves to
  the generation module in step 10) remains as a deprecation shim so
  every existing scenario is untouched. No RNG changes; hashes gate.
- **A-002 (second half):** `Protocol` type for the surviving
  `state.legacy` surface (registries, `_resolve_effective_specs`,
  `main`) — written against the post-epic module layout so it is typed
  once, correctly. Add the newly-typed modules to the mypy gate list.

## Boundaries And Non-Goals

- No behavior changes anywhere; locked hashes gate every PR.
- No scenario-author-facing API breaks (introspection shim stays).
- No typing of `legacy.py`'s interior (that is the epic's mypy story).

## Affected Files

Per PR: `otel_stream.py`+`server.py`+`legacy.py` (PR 1);
`cli_args.py`+`server.py` (PR 2); `scenarios_impl.py`+consumers (PR 3);
generation module+`server_ops.py`/`server_mcp.py` typing (PR 4); mypy
gate list additions each PR; `.trellis/audit/ledger.md` flips
(A-002/005/006/007/008/009/010 across the four PRs).

## Risks And Edge Cases

- PR 1's A-008 divergence reconciliation is the only place a behavior
  question can hide — resolve it by reading, and pin the chosen
  behavior with a test before unifying.
- PR 3 touches the monkeypatch-heavy scenario surface — the spec objects
  must keep the dict-compatible access the tests use, or the PR includes
  the enumerated test migration (same policy as decomp step 10).
- Sequencing risk: if the epic stalls, PRs 2–4 stall with it — that is
  accepted and recorded (the PRD says "natural companion"); PR 1 alone
  still closes A-008/009/010 + half of A-002.

## Validation

- Full suite (hashes) per PR; mypy gate green with each list addition;
  `pytest tests/test_server_mcp.py` for the A-002 import retargets.
