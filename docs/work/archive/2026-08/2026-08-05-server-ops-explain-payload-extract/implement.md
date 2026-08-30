# Extract server_ops_explain.py + server_ops_payloads.py — Implementation Plan

Every line number below is against `main` at `server_ops.py` = 4,687 lines.
Re-measure with the closure audit before cutting if the file has moved.

## Execution Order

0. **Branch + baseline.** Branch from synced `main`. Record
   `wc -l src/anomaly_metric_creator/server_ops.py`. Extract the `__all__`
   block to a baseline file for the byte-identity check in the
   Validation Plan:

   ```bash
   .venv/bin/python - <<'PY' > /tmp/all_before.txt
   import ast, pathlib
   src = pathlib.Path("src/anomaly_metric_creator/server_ops.py").read_text()
   node = next(
       n for n in ast.parse(src).body
       if isinstance(n, ast.Assign)
       and any(getattr(t, "id", None) == "__all__" for t in n.targets)
   )
   # Raw source segment, NOT literal_eval: the requirement is byte identity,
   # so quote style, formatting, comments, and list-vs-tuple must all count.
   print(ast.get_source_segment(src, node), end="")
   PY
   ```

   **Cut ordering rule (blocking).** All three ranges are line numbers
   against the *unmodified* file. Every cut shifts every later line
   number, so the ranges are only simultaneously valid before the first
   edit. Therefore: **copy all three blocks out to scratch files first**
   (`sed -n '1944,2101p'`, `'2516,2570p'`, `'2705,2796p'`), then perform
   the cuts **bottom-up** — manifest (2705-2796), then JSON-pointer
   (2516-2570), then explain (1944-2101) — so each deletion only moves
   lines below ranges already handled. Never re-derive a range by eye
   from a partially edited file; if a range must be recomputed mid-flight,
   re-run the closure audit and relocate by symbol name.

1. **Pre-flight audit (read-only, blocking).**
   - Re-run the closure audit for both seed sets; confirm 10/4/2 defs and
     that neither closure contains `SimulationState` or `resource_snapshot`.
   - Monkeypatch preflight: grep for the **moved symbol names themselves**
     across the whole suite, not for `setattr(` lines — a patch target can
     sit on the line *after* the call, as at `tests/test_server.py:585-586`,
     so a `setattr(`-only grep cannot prove absence.
     `grep -rn -e _openapi_schema_from_value -e _explain_field_description
     -e _explain_title -e _explain_schema_at_path -e _format_explain
     -e _format_recursive_explain_fields -e _explain_properties
     -e _explain_display_schema -e _explain_type_label -e _explain_type_name
     -e _apply_json_patch -e _json_pointer_parts -e _set_json_pointer
     -e _remove_json_pointer -e _load_manifest_documents
     -e _normalize_manifest_documents tests/` must return no hits.
   - Splice-hazard grep: `sed -n '1944,2101p;2516,2570p;2705,2796p'
     src/anomaly_metric_creator/server_ops.py | grep -n "^from \."` must
     be empty.
   Any hit here stops the cut and reopens the design.

2. **Build the render oracle (before any edit).** Scratch script driving
   `run_command` under a frozen clock over the fixed corpus below,
   capturing `(stdout, stderr, exit, matched_rule_id, support_status)` per
   command to a file. Run it on the untouched tree, in both normal and
   `--mcp-eval-mode` states. This is the behavior-identity evidence; the
   test suite alone is not sufficient (no golden hashes exist on this
   surface).

   Corpus:
   - `kubectl explain pods`, `... pods.spec`, `... pods.spec.containers`,
     `... pods --recursive`, `... deployments.status`,
     `... widgets.example.com` (unsupported path).
   - `kubectl patch deployment cacheservice -n saas-prod --type=json -p
     '[{"op":"replace","path":"/spec/replicas","value":4}]'`,
     the same with `--type=merge` and a merge-patch body, an invalid
     pointer (`/spec/nope/deep`), and a malformed `-p` body.
   - `kubectl apply -f <manifest>.json`, `<manifest>.yaml`, a missing
     path, a `.txt` extension, and a malformed YAML document. (The
     PyYAML-absent branch at `server_ops.py:2722` is unreachable with the
     dev extra installed and is out of oracle scope — see design.md
     § Risks.)

3. **Create `server_ops_payloads.py`.** Docstring; `from __future__ import
   annotations`; `import json`; `from pathlib import Path`;
   `from typing import Any`; `from .server_command_render import
   CommandResult`; then block A (`2516-2570`, JSON-pointer ops) and block
   B (`2705-2796`, manifest documents) verbatim, in that order. Keep the
   in-function lazy `import yaml` exactly as written.

4. **Cut and stub leaf 2 — manifest block first.** Delete `2705-2796`
   from `server_ops.py` and place a
   `from .server_ops_payloads import (...)` stub at that position so
   `_manifest_apply_targets` (:2693) keeps resolving its callees in
   `server_ops`'s namespace.

5. **Cut and stub leaf 2 — JSON-pointer block.** Delete `2516-2570` and
   place a second `from .server_ops_payloads import (...)` stub at that
   position so `_patch_payload` (:2442) keeps resolving.

6. **Create `server_ops_explain.py`.** Module docstring naming the epic
   step and the one-way rule; `from __future__ import annotations`;
   `from typing import Any`; then the 10 defs pasted verbatim from the
   step-0 scratch copy of `server_ops.py:1944-2101`, in source order.

7. **Cut and stub leaf 1.** Delete `1944-2101` from `server_ops.py` and
   put a `from .server_ops_explain import (...)` block at exactly that
   position (immediately after `_minimal_k8s_object`), listing all 10
   names in source order with the `X as X` re-export form used by the
   existing stubs (see `server_ops.py:156`).

8. **Clean up orphans.** `.venv/bin/ruff check
   src/anomaly_metric_creator/` and remove only imports ruff reports
   unused. Do not hand-prune.

9. **Gate + docs.** Add both leaves to `CLEAN_MODULES` in
   `tools/check_mypy_gate.py` (34 modules). Update the CLAUDE.md module
   ownership map row for the server leaves, and
   `.trellis/spec/amc/backend/architecture.md` § Module Boundaries (leaf
   DAG + import directions, near the existing `server_command_render` /
   `server_k8s_api_trace` entries).

10. **Epic tracker.** In
   `.trellis/tasks/07-06-server-ops-decomposition/implement.md`: add the
   step 6a entry with measured sizes; rewrite the step 6 line as **6b**,
   recording the `resource_snapshot` seam blocker and that the
   render-dispatch split needs a provider-seam design decision; replace
   the `[ ] Steps 6–7 pending` line accordingly; and correct the stale
   post-step-5 figure (the tracker says `server_ops.py` ended at 4,693,
   the live file is 4,687 after later merges).

## Validation Plan

```bash
# focused first
.venv/bin/pytest tests/test_server.py tests/test_server_ops_fuzz.py \
  tests/test_server_mcp.py tests/test_server_eval_mode.py -n 0
# oracle diff (must be byte-identical, both states)
.venv/bin/python <scratch>/render_oracle.py --check
# gates
python3 tools/check_mypy_gate.py
.venv/bin/pytest
.venv/bin/pre-commit run --all-files
git diff --check
# Compatibility surface untouched. TWO commands are required: `main...HEAD`
# is merge-base-to-HEAD and covers only *committed* work, while `git diff HEAD`
# covers staged + unstaged. Either alone leaves a hole a forbidden edit fits in.
SURFACE="src/anomaly_metric_creator/server.py \
  src/anomaly_metric_creator/server_commands.py \
  src/anomaly_metric_creator/server_kubernetes.py \
  src/anomaly_metric_creator/server_helm.py \
  src/anomaly_metric_creator/server_mcp.py"
git diff --stat main...HEAD -- $SURFACE   # committed: must be empty
git diff --stat HEAD -- $SURFACE          # staged + unstaged: must be empty
# __all__ byte-identical: compare the parsed list against the step-0 baseline.
# A diff grep for the literal '__all__' is NOT sufficient — an edit deep inside
# the 227-entry list at server_ops.py:4459 never puts the assignment line in a
# diff hunk, so the grep would pass while the surface changed.
.venv/bin/python - <<'PY' > /tmp/all_after.txt
import ast, pathlib
src = pathlib.Path("src/anomaly_metric_creator/server_ops.py").read_text()
node = next(
    n for n in ast.parse(src).body
    if isinstance(n, ast.Assign)
    and any(getattr(t, "id", None) == "__all__" for t in n.targets)
)
print(ast.get_source_segment(src, node), end="")
PY
diff /tmp/all_before.txt /tmp/all_after.txt && echo "__all__ byte-identical"
```

## Documentation And Spec Updates

- CLAUDE.md § Module ownership map — server leaf row.
- `.trellis/spec/amc/backend/architecture.md` § Module Boundaries — both
  leaves, sizes, import direction.
- Epic `implement.md` step status (6a added, 6 rewritten as 6b).
- PR body: cluster moved, measured sizes before/after, monkeypatch grep
  result, splice-hazard grep result, oracle diff status — and an explicit
  statement that the behavior-identical claim rests on the oracle plus the
  fuzz corpus.

## Review Notes

- The reviewer-sensitive claim is **verbatim**: the diff should read as a
  pure relocation. Any content change inside a moved hunk needs calling
  out in the PR body.
- The step-6 deviation is the other reviewer-sensitive item: this PR does
  not do what the epic's design.md step 6 says. The PRD carries the
  falsifying audit; the PR body must link it rather than quietly
  substituting a smaller scope.

## Rollback Points

- After step 5 (leaf 2 fully cut and stubbed — both blocks) and after
  step 7 (leaf 1 cut and stubbed): both are independent commits, either
  revertible alone. Do **not** checkpoint between steps 4 and 5: after
  step 4 the manifest block is cut but the JSON-pointer block is not, so
  `server_ops_payloads.py` is only half wired.
- The whole PR is one revert away from `main`; nothing else depends on the
  new modules.

## Follow-Ups

- **Step 6b** — `resource_snapshot` provider seam + render-dispatch split
  (needs a maintainer design decision on the seam and its monkeypatch
  semantics).
- `tests/test_server.py:230`
  (`test_kubectl_logs_named_pod_takes_precedence_over_selector`) patches
  `server.resource_snapshot`, but the code path resolves
  `resource_snapshot` in `server_ops`'s namespace, so the patch cannot
  bite and the guard is inert. Out of scope for a verbatim-move PR;
  file as its own task.
- No test covers the PyYAML-absent branch of `_load_manifest_documents`
  (`server_ops.py:2722`); it needs an import-blocking fixture. Out of
  scope for a verbatim-move PR.
- Step 7 epic close-out (end size, final map, archive).
