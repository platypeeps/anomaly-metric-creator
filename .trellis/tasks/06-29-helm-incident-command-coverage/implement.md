# Helm incident command coverage — Implementation Plan

## Execution Order

1. Branch from `main`. Map the release/chart state the Secret encoder +
   `helm get values` already read; list the fields each new render needs.
2. Implement `helm show chart`, `helm show values`, `helm get metadata`
   renderers (release/chart-state-backed; overlay-aware revisions;
   `_exposed_*` accessors for anything scenario-adjacent).
3. Classify `helm lint` / `dependency` / `repo` as unsupported with
   Helm-shaped stderr; confirm they land in the unsupported backlog
   grouping.
4. Tests: supported renders (byte-stable), chart-not-found, unsupported
   traces, eval-mode redaction assertion.
5. Manual cross-check against the pinned Helm 4 binary; paste output
   comparison into the PR.
6. Draft PR → checklist → ready → merge.

## Validation Plan

```bash
.venv/bin/pytest tests/test_server.py -n 0 -k "helm"
.venv/bin/pytest tests/test_server_eval_mode.py -n 0
.venv/bin/pytest && .venv/bin/pre-commit run --all-files
```

## Documentation And Spec Updates

- README serve section's Helm command list; CLAUDE.md only if it
  enumerates Helm families (grep first).

## Review Notes

- The Helm-4 output-shape fidelity is the review question — the manual
  comparison transcript answers it.

## Follow-Ups

- Additional families only when the unsupported backlog shows real
  demand (that visibility is what step 3 buys).
