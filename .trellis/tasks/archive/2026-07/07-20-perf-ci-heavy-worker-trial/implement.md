# Heavy-lane two-worker trial — Implementation Plan

## Execution order

1. Branch from `main` after the light-worker PR merges.
2. Add temporary before/after runner diagnostics and the one-second
   `/proc/meminfo` peak sampler to the heavy job.
3. Change the heavy command to `pytest -n 2 --dist loadfile -m heavy` while
   preserving coverage arguments.
4. Run focused CI-contract tests and local heavy/light/full collection checks.
5. Publish with `full-ci`, capture the heavy job log and wall time, and apply
   the pre-committed 12 GB / 2 GB decision rule.
6. Remove diagnostics. Keep `-n 2 --dist loadfile` only if the rule passes;
   otherwise restore `-n 0`. Record exact evidence in this PRD, `CLAUDE.md`,
   and the parent performance task.
7. Re-run the deterministic gate and final full matrix before merge.

## Local validation

```bash
.venv/bin/pytest -n 2 --dist loadfile -m heavy -q
.venv/bin/pytest -m heavy --collect-only -q
.venv/bin/pytest -m "not heavy" --collect-only -q
.venv/bin/pytest --collect-only -q
.venv/bin/pytest -q tests/test_ci_review_contract.py tests/test_heavy_marker.py
```

CI is authoritative for capacity. Do not call a local pass evidence that the
16 GB / 14 GB runner has sufficient headroom.
