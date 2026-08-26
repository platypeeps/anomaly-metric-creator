# Persisted server mutation state — Implementation Plan

## Execution Order

1. Branch from `main`. Audit `SimulationMutations` commit paths for
   lock coverage (design.md risk); flag any outside-lock write as its
   own finding before proceeding.
2. Implement serialize/hydrate on `SimulationMutations` (versioned
   envelope, dataclass-field-driven so a new overlay field fails loudly
   at serialization rather than silently dropping).
3. Wire `--persist-mutations` (serve flag parser + `build_state` load +
   per-commit atomic write under the lock + reset truncation).
4. Stale-component drop + WARNING; corrupt/version refusal paths.
5. Tests per design.md Validation (continuity, reset, refusals,
   flag-off byte-identical default).
6. Docs: README serve section (opt-in, reset semantics, keep the file
   outside `--output-dir`); ops spec file; coordinate the reset wording
   with the reset task if it landed first.
7. Draft PR → checklist → ready → merge.

## Validation Plan

```bash
.venv/bin/pytest tests/test_server_mutation_persistence.py -n 0
.venv/bin/pytest tests/test_server.py -n 0
.venv/bin/pytest && .venv/bin/pre-commit run --all-files
```

Manual: mutate via real kubectl scale, restart serve with the flag,
re-inspect — continuity visible in `kubectl get deployments`.

## Documentation And Spec Updates

- README + operations spec; CLAUDE.md server-mode paragraph gains one
  sentence (opt-in persistence exists; overlay-only).

## Review Notes

- The envelope-versioning and atomic-write choices mirror existing repo
  patterns (trace store, artifact writers) — cite both in the PR so
  review anchors on precedent.

## Follow-Ups

- If workshops want persisted *clock* continuity later, that is a
  separate design (explicitly excluded here).
