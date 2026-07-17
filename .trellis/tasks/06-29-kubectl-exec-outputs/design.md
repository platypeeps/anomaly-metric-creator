# Realistic kubectl exec responses — Design (SD Work Designs, 2026-07-17)

## Overview

`kubectl exec` already renders env/curl/generic outputs (`_render_exec`,
server_ops.py:4089). The PRD's verified notes pin two defects that come
first, and the eval-wall coupling is already resolved upstream (exec env
output routes through `_exposed_active_scenarios` since the wall
completeness task) — new outputs must stay inside that wall.

## Proposal

1. **Fix argument fidelity (the two verified defects):**
   - The `"--" in parsed.positionals` guard (server_ops.py:4092) is dead
     — `--` sits in `_BOOL_FLAGS` (:1102) so `_split_flags` consumes it.
     Fix the parse: treat `--` as a hard separator in `_split_flags` —
     everything after it is verbatim positional payload (no flag
     interpretation), which also fixes defect 2 (`kubectl exec pod --
     ls -la` currently loses `-la` to the generic flag arm at :1474).
     Then the guard becomes live again (or is removed in favor of the
     parsed separator field — pick whichever keeps `parse_command`'s
     output shape stable for other consumers; audit fingerprint/redaction
     impact since parsed shape feeds `command_fingerprint`).
   - Fuzz corpus gains post-`--` flag shapes.
2. **One new incident probe, scenario-mapped:** `df -h` — renders a
   filesystem table whose usage percentages are shaped by the component's
   behavior state (elevated under the storage-pressure profile via the
   existing profile/behavior helpers — symptoms only, no slugs).
   Deterministic values derived from component + simulated clock bucket.
3. **Everything else stays explicit:** unknown pods → NotFound;
   unsupported commands → the existing generic-unsupported exec arm with
   partial/unsupported trace (no arbitrary shell — PRD guardrail).

## Boundaries And Non-Goals

- No shell emulation, no new pod/container state, no interactive/TTY
  modes (`-it` stays unsupported-flagged).
- Wall rule: no active-scenario identifiers in any new output — only
  observable symptoms.

## Affected Files

`src/anomaly_metric_creator/server_ops.py` (`_split_flags`,
`_render_exec`), `tests/test_server.py`,
`tests/test_server_ops_fuzz.py`, `tests/test_server_eval_mode.py`
(exec output in the ops leak sweep already covers env; extend to the new
probe).

## Risks And Edge Cases

- `_split_flags` is shared by every command family — the `--` separator
  change must be exercised against helm/kubectl shapes broadly (the fuzz
  corpus is the net; run it first, then targeted assertions for exec).
- `command_fingerprint` stability: post-`--` tokens joining the
  fingerprint changes grouping for previously-lossy commands — that is
  the *fix* (they were wrongly collapsed), but say so in the PR and
  check no locked trace-fixture assertions depend on the lossy form.

## Validation

- `pytest tests/test_server.py -n 0 -k exec`, fuzz corpus, eval sweep;
  full suite.
- Manual: `kubectl exec <pod> -- ls -la` and `df -h` via real kubectl
  against a live serve.
