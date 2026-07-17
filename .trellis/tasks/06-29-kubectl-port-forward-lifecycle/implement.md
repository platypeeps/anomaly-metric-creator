# kubectl port-forward lifecycle — Implementation Plan

## Execution Order

1. Branch from `main`. Locate the snapshot's port fields (services +
   containers) and confirm coverage for the modeled components.
2. Implement the renderer: target/port validation → startup lines +
   simulator no-tunnel line → exit 0; partial classification with the
   explanatory matched rule.
3. Port-spec parser (`LOCAL:REMOTE` forms) + kubectl-shaped errors;
   decide multi-port rendering by output cost; unsupported shapes
   (`--address`, UDS) → unsupported traces.
4. Tests: success shape, NotFound, port miss, malformed specs (fuzz),
   classification assertions.
5. Draft PR → checklist → ready → merge (grep-for-socket note in the PR
   per design.md).

## Validation Plan

```bash
.venv/bin/pytest tests/test_server.py -n 0 -k "port_forward"
.venv/bin/pytest tests/test_server_ops_fuzz.py -n 0
.venv/bin/pytest && .venv/bin/pre-commit run --all-files
git diff | grep -iE 'socket|bind\(' || echo "no socket surface"
```

## Documentation And Spec Updates

- README serve kubectl notes: the honest no-tunnel semantics sentence.

## Review Notes

- The security posture (zero sockets, partial classification) is the
  review headline; design.md carries the rationale.

## Follow-Ups

- A real proxy mode only as its own consented design with SECURITY.md
  review — explicitly out of scope here.
