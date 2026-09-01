# Symptom-level log artifact — Implementation Plan

## Execution Order

1. **Decision gate:** present design.md's four answers to the
   maintainer (adopt / adjust / decline); record the decision in the
   PRD. On decline, record rationale in the PRD + eval-mode docs and
   close (PRD's decline arm).
2. Branch from `main`. Build `symptom_log.py` (episode detector +
   writer on `csv_layout` readers; MAD floor guard) with unit tests on
   synthetic columns first.
3. Wire the `symptomlog` emit token + registry integration (artifact
   files, atomic writer, pre-clean, summary, schema.json files) in one
   pass; N=3 long-form coverage.
4. Tune thresholds against the scenario-deviation data (headline
   scenarios produce episodes; natural-only runs produce few/none);
   then lock 1d/7d SHA-256 hashes.
5. Serve integration: eval-mode log-source dispatch (tools + SSE serve
   `symptom.log` when present; refusal fallback when absent); non-eval
   unchanged.
6. Leak tests (artifact-content sweep + tool-response sweep) with
   non-vacuous guards; extend the registry-driven eval sweep table if
   `07-17-audit-mcp-wall-registry-guard` has landed.
7. Docs: README artifact table + eval section; CLAUDE.md log
   classification update.
8. Draft PR (`full-ci`) → checklist → ready → merge.

## Validation Plan

```bash
.venv/bin/pytest tests/test_symptom_log.py -n 0
.venv/bin/pytest tests/test_server_eval_mode.py tests/test_server_mcp.py -n 0
.venv/bin/pytest tests/test_schema_file.py tests/test_atomic_writes.py -n 0
.venv/bin/pytest && .venv/bin/pre-commit run --all-files
```

## Documentation And Spec Updates

- README, CLAUDE.md (artifact registry + eval-mode paragraphs),
  `.trellis/spec/amc/backend/` artifact conventions.

## Review Notes

- The structural-cleanliness argument (writer never reads the manifest)
  plus the two leak tests are the security story — lead with them.
- Registry completeness is checklist-heading material (single source of
  truth) — show the one-pass registry diff.

## Follow-Ups

- Configurable thresholds / per-metric sensitivity only on real eval
  demand.
