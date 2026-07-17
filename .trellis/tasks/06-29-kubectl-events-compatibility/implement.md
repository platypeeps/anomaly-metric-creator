# kubectl events compatibility — Implementation Plan

## Execution Order

1. Branch from `main`. Read the current event renderer + overlay-event
   merge; list existing order-sensitive test assertions.
2. Pin the deterministic default sort (lastTimestamp asc, name tiebreak);
   update the enumerated assertions.
3. Add `--for` (via `_KIND_ALIASES`, snapshot-resolved, kubectl-shaped
   empty result) and `--types`; register flags in the tables.
4. `--sort-by` for the two supported JSONPaths; other values → partial
   downgrade; fuzz shapes for malformed `--for`/`--sort-by`.
5. Tests: filter/sort determinism, both entry paths, partial/unsupported
   visibility.
6. Manual real-kubectl check via kubeconfig; paste transcript.
7. Draft PR → checklist → ready → merge.

## Validation Plan

```bash
.venv/bin/pytest tests/test_server.py -n 0 -k "event"
.venv/bin/pytest tests/test_server_ops_fuzz.py -n 0
.venv/bin/pytest && .venv/bin/pre-commit run --all-files
```

## Documentation And Spec Updates

- README serve kubectl notes if they enumerate supported flags.

## Review Notes

- The default-sort change is the only behavior delta existing users can
  see — call it out with the before/after order rationale.

## Follow-Ups

- Field-selector emulation only on demonstrated demand via
  partial-trace backlog.
