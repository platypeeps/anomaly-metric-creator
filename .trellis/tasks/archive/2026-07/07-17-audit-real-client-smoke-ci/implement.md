# Real kubectl/Helm smokes in CI + K8s version bump — Implementation Plan

## Execution Order

1. Branch from `main`. Pin the implementation-time official stable clients:
   kubectl v1.36.2 and Helm v4.2.0, with an advertised Kubernetes v1.36.2;
   record all three in the PR description.
2. A-067: hoist `_K8S_ADVERTISED_VERSION`, update both literal sites,
   grep-sweep `1.29.4`.
3. Local proof: download the pinned binaries to a scratch dir, run both
   smokes with `AMC_RUN_REAL_CLIENT_SMOKE=1` against the bumped facade;
   capture stderr (skew-warning check).
4. A-022: add the full-lane step to ci.yml (pinned curl + sha256 verify +
   retry, env var, `pytest -n 0 -k` the two smokes). Comment the pins
   with the bump-checklist pointer; add both to DEVELOPMENT_CYCLE's
   "Pinned tools bump" list.
5. README: tested client versions sentence.
6. Flip A-022/A-067 → `fixed` (same PR).
7. Draft PR (workflow diff → full matrix runs the new step by
   construction) → checklist (CI-hygiene heading) → ready → merge.

## Validation Plan

```bash
rg '1\.29\.4' src/                                   # empty
AMC_RUN_REAL_CLIENT_SMOKE=1 .venv/bin/pytest -n 0 -k "real_client" tests/test_server.py
.venv/bin/pytest tests/test_server.py -n 0           # non-smoke regressions
.venv/bin/pytest && .venv/bin/pre-commit run --all-files
# CI: verify the step appears in the full lane and not the quick lane
```

## Documentation And Spec Updates

- README tested-versions; DEVELOPMENT_CYCLE bump list; CLAUDE.md's
  "smoke-tested with Helm 4" sentence gets the version specifics if it
  names any.

## Review Notes

- Checksums + retry are the reviewer-sensitive part (supply chain +
  flake surface) — annotate them inline in the workflow.

## Follow-Ups

- Broaden smoke coverage from the unsupported-trace backlog once the
  lane exists (new task when demand shows).
