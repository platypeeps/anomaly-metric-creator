# Decompose server_ops.py — Epic Design (SD Work Designs, 2026-07-17)

## Overview

`server_ops.py` measured today: **7,699 lines**. The extraction machinery
is a straight reuse of the legacy.py epic's proven pattern with one
role-swap: **`server_ops.py` plays the part `legacy.py` played** — code
moves *out* into leaf modules, `server_ops.py` re-imports every moved name
at the same conceptual location, and new modules never import
`server_ops`. That single rule keeps the entire compatibility surface
working with **zero edits**:

- `server.py`'s manual alias block (`NAME = _server_ops.NAME`,
  server.py:282+) reads attributes off `server_ops` — untouched.
- The focused facades (`server_commands.py`, `server_kubernetes.py`,
  `server_helm.py`) do `from .server_ops import (...)` — untouched.
- `server_mcp.py` imports `from .server_ops import (...)` (:35) —
  untouched.
- Test patch sites (5× `setattr(server, …)`, 1×
  `setattr(server._server_ops, …)`) keep resolving; the
  move-with-callers rule governs whether a patch still *bites* (see
  Monkeypatch below).

## Proposal — boundaries and sequencing (leaf-first, data-first)

One module per PR, in this order; measured sizes decide the flagged
splits at implementation time:

1. **`server_ops_profiles.py`** (~700) — `OpsComponentImpact`,
   `OpsScenarioProfile`, `_impact`, `_profile`,
   `OPS_SCENARIO_PROFILES`, `validate_ops_profiles` (validator moves with
   its registry, same-PR, preserving import-time execution position).
   Pure-data leaf; proves the pattern. If it lands >800, invoke the
   PRD's data-registry exemption explicitly in the PR + CLAUDE.md.
2. **`server_ops_parse.py`** (~450) — `parse_command`, `_split_flags`,
   the flag tables (`_VALUE_FLAGS`/`_BOOL_FLAGS`/…), `command_fingerprint`,
   `_redact_parsed_flags`, `guess_intent`. Stdlib-only leaf.
3. **`server_helm_impl.py`** (~400) — Helm renderers + release-Secret
   encoding + helm metrics. (`server_helm.py` facade keeps importing
   through `server_ops`; no facade edit.)
4. **`server_k8s_objects.py` + `server_k8s_tables.py`** (~500 + ~450) —
   REST object builders and `meta.k8s.io` Table rendering. The PRD's
   single-module candidate exceeds the cap combined (~950), so plan the
   two-file cut along the object-vs-table seam from the start; one PR.
5. **`server_k8s_api.py`** (~710) — discovery/OpenAPI/kubeconfig + the
   REST facade helpers (`_k8s_api_resource_list`,
   `_k8s_objects_for_resource`, …). Imports steps 4's modules one-way.
6. **`server_ops_render.py` + `server_ops_render_workloads.py`**
   (~830 + ~1,000 → split mandatory) — render dispatch +
   `_render_get`/`_render_describe` in the first; logs/rollout/mutation
   renderers in the second. Naming may collapse to one file only if the
   measured combined size lands <800 (it will not).
7. **End state:** `server_ops.py` retains the runtime dataclasses +
   `SimulationState` (~250), `resource_snapshot()` + shared helpers
   (~460), and the re-import wiring — target ≤ ~800; record the measured
   end size in the epic close-out (mirror of legacy epic Decision 2, but
   the numbers here should actually fit).

## Monkeypatch and identity rules (compatibility inventory)

- Grep `tests/test_server*.py` + `tests/test_trace_bundle.py` for
  `setattr` targets before **each** step; any patched name moves together
  with all its intra-`server_ops` callers (legacy-epic rule) or stays.
  The known `setattr(server._server_ops, …)` site gets its exact name
  resolved at step-1 time and its step notes the consequence.
- `server.py`'s alias block: **no changes during the epic** — it reads
  `server_ops` attributes which the re-import stubs preserve. The PRD's
  "re-export module / `__getattr__` delegation" idea is recorded as a
  follow-up (behavior-affecting import-surface change; out of epic
  scope).
- `state.legacy` is orthogonal (that's the generator seam); no
  interaction.

## Boundaries And Non-Goals

- Zero HTTP/command/MCP behavior change; renderer output bytes identical
  (the fuzz corpus and server tests are the oracle — no golden hashes
  exist on this surface, so the test suite + targeted before/after render
  diffs for a sample command set are the evidence).
- The four-parallel-surfaces-per-kind collapse (single per-kind
  descriptor) stays a named follow-up epic, not this one.
- `server.py` itself (1,791 lines) is not decomposed here; its seam
  questions are follow-up material once `server_ops` is done.

## Affected Files

Per step: the new module(s), `server_ops.py` (delete + re-import),
CLAUDE.md module map, `.trellis/spec/amc/backend/architecture.md`. Never:
the three facades, `server.py`'s alias block, `server_mcp.py` imports.

## Risks And Edge Cases

- Splice hazard on every cut (grep deleted ranges for `^from \.`).
- Import-time `validate_ops_profiles()` execution position (step 1) — the
  re-import must sit where the registry block sat.
- Hidden intra-file coupling: renderers reach into snapshot helpers and
  parse structures; the one-way rule means shared helpers move *down*
  into leaves (or stay in `server_ops.py`), never sideways — audit each
  step's closure with a quick AST/grep pass before cutting.
- `tests/test_server_ops_fuzz.py` seeds malformed inputs through
  `parse_command`/`run_command` — it is the regression net for steps 2
  and 6; run it serially per step.

## Validation

Per step:

```bash
.venv/bin/pytest tests/test_server.py tests/test_server_ops_fuzz.py \
  tests/test_server_mcp.py tests/test_server_eval_mode.py -n 0
.venv/bin/pytest    # full suite
```

Plus a before/after stdout byte-diff of a fixed sample command list
(`kubectl get pods/deployments/events`, `describe`, `logs`, `helm
list/status/history`) captured via `run_command` in a scratch script —
the server-layer analog of the golden hashes.
