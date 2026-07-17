# Type the spec/config/server boundaries — Implementation Plan

## Execution Order

**PR 1 (now):** A-008 ritual diff → canonical-behavior decision → shared
builder + pin test → both callers consume it. A-009 keyword-only params
(grep call shapes first). A-010 `__all__` + public aliases in the three
leaves. A-002 first half: direct leaf imports in server_mcp/server_ops.
Flip A-008/A-009/A-010; note A-002 as half-done in the ledger entry
(keep `open` with a dated progress note until PR 4).

**PR 2 (after decomp step 8 merges):** RunConfig in `cli_args.py`;
migrate `serve_main`/`build_state` off the getattr fallbacks (~15
sites); each default routed through its `DEFAULT_*` constant. Flip
A-007.

**PR 3 (with/after decomp step 9):** frozen AnomalySpec/CascadeSpec;
`_validate_scenario_spec` → pure parser; provenance fields replace the
runtime-stamped keys; test-migration inventory if dict-access
compatibility cannot be kept. Flip A-005.

**PR 4 (after decomp step 10):** generator-arity opt-in + introspection
shim; `state.legacy` Protocol against the settled layout; mypy-gate
additions. Flip A-006 and A-002.

Every PR: full suite (hashes), draft → checklist → ready → merge.

## Validation Plan

```bash
.venv/bin/pytest                                    # hashes gate every PR
.venv/bin/python tools/check_mypy_gate.py           # once it exists (A-048)
.venv/bin/pytest tests/test_server_mcp.py tests/test_otel_gauges.py -n 0
.venv/bin/pre-commit run --all-files
```

PR 1 extra: a pinned-behavior test for the A-008 unified builder BEFORE
the unification commit (red/green shows the divergence resolution).

## Documentation And Spec Updates

- CLAUDE.md: RunConfig seam, spec dataclasses, dispatch opt-in — each in
  the PR that lands it; `.trellis/spec/amc/backend/` conventions for the
  new typed seams.

## Review Notes

- Each PR's description states its epic-timeline precondition and links
  the decomp PR it follows — reviewers must be able to see the
  no-double-churn rule holding.

## Follow-Ups

- Full mypy coverage of `server.py` (A-020's noted gap) once RunConfig
  lands — candidate addition to the gate list in PR 2.
