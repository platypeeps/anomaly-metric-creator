# Realistic kubectl exec responses — Implementation Plan

## Execution Order

1. Branch from `main`. Audit `_split_flags` consumers + fingerprint
   impact of the `--` separator change; write the fidelity fix; run the
   fuzz corpus immediately.
2. Remove/revive the dead `--` guard in `_render_exec` per the chosen
   parse shape; add post-`--` fuzz shapes + exec fidelity assertions
   (`ls -la` reconstructed verbatim).
3. Add the `df -h` probe (behavior-state-shaped, deterministic,
   symptom-only); extend the eval ops leak sweep to it.
4. Tests: supported/partial/unsupported exec matrix per the PRD
   acceptance.
5. Manual real-kubectl transcript for both fixes.
6. Draft PR → checklist → ready → merge.

## Validation Plan

```bash
.venv/bin/pytest tests/test_server_ops_fuzz.py -n 0     # first — separator blast radius
.venv/bin/pytest tests/test_server.py -n 0 -k "exec"
.venv/bin/pytest tests/test_server_eval_mode.py -n 0
.venv/bin/pytest && .venv/bin/pre-commit run --all-files
```

## Documentation And Spec Updates

- README serve exec notes if they list supported probes.

## Review Notes

- Lead with the `_split_flags` blast-radius audit — it is shared
  parsing; the fuzz-first ordering is the safety story.

## Follow-Ups

- More probes (`ps aux`, `free -m`) only on workshop demand via the
  unsupported backlog.
