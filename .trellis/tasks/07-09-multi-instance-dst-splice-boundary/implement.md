# Multi-instance DST splice boundary — Implementation Plan

## Execution Order

1. **Decision gate:** confirm keep-unsupported with the maintainer
   (design.md carries the structural rationale); record in the PRD. If
   overridden → this task becomes a full non-monotonic-model design
   effort; stop and re-plan.
2. Branch from `main`. Coverage grep: list existing tests for the two
   parse-time rejection paths × DST and the `generate_component`
   ValueError; add only the missing cases.
3. Language sweep: standardize "intentional design boundary" across
   README, both CLAUDE.md sites, and the spec file; verify error
   messages point at `--inject-dst-artifact-day 0`.
4. Record the decision + rationale in the PRD.
5. Draft PR → checklist (doc-sync heading) → ready → merge.

## Validation Plan

```bash
.venv/bin/pytest tests/test_args.py tests/test_instances_per_component.py -n 0
rg -n "intentional design boundary" README.md CLAUDE.md .trellis/spec/
rg -n "only remaining gate" README.md CLAUDE.md .trellis/spec/   # expect empty
.venv/bin/pytest -m "not heavy" -n 2 && .venv/bin/pre-commit run --all-files
```

## Documentation And Spec Updates

This task is mostly docs; the spec file edit makes the posture
discoverable from canonical guidance (PRD requirement).

## Review Notes

- Wording-only diff on load-bearing CLAUDE.md paragraphs — flag that in
  the PR so review focuses on phrase substitution fidelity.

## Follow-Ups

- None under keep-unsupported. The recorded rationale is the durable
  artifact.
