# OTEL exporter target policy — Implementation Plan

## Execution Order

1. **Decision gate:** confirm "warn" with the maintainer (audit + this
   design both recommend it; "accept" fallback is fully specified in
   design.md). Record the answer in the PRD.
2. Branch from `main`. Thread the endpoint-origin marker through
   `_reconcile_cli_surface` (additive dests in `set_defaults`).
3. Resolved-target startup lines per selected signal in the streamers
   (both CLI and serve paths hit the same helpers).
4. Warn conditions 1 (env-sourced) and 2 (remote, no token); zero
   warnings for unselected signals / `--otel-send none`.
5. Tests per design.md Validation matrix; precedence regressions.
6. SECURITY.md "OTEL egress" paragraph + README CLI note (include the
   no-allowlist rationale).
7. Draft PR → checklist → ready → merge.

## Validation Plan

```bash
.venv/bin/pytest tests/test_cli.py tests/test_otel_gauges.py -n 0
.venv/bin/pytest tests/test_args.py -n 0        # namespace-shape safety
.venv/bin/pytest && .venv/bin/pre-commit run --all-files
```

## Documentation And Spec Updates

- SECURITY.md, README CLI reference; CLAUDE.md CLI-surface section gets
  the origin-marker dests mentioned beside the existing set_defaults
  seeding sentence.

## Review Notes

- Keep the diff visibly free of precedence changes — the PR description
  should state "resolution order untouched; only observed and reported".

## Follow-Ups

- Allowlist mode only if the tool's deployment story ever changes
  (recorded rejection in design.md).
