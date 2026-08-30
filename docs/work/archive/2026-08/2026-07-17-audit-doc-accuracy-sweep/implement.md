# Stale security/reviewer/reference docs — Implementation Plan

## Execution Order

1. Branch from `main`. Commit 1 (docs): work the ledger items in evidence
   order — A-026 SECURITY.md posture rewrite (verify against
   redaction.py before writing), A-027 Copilot instructions + contract
   anchors, A-028 pyproject comments, A-029 CLAUDE.md aggregate naming,
   A-030 README dev-extra, A-064 uv-locked primary instruction, A-069
   OTEL table row.
2. Commit 2 (A-046): pick floors from uv.lock + py3.14 support matrix;
   edit pyproject; `uv lock --check`; CHANGELOG Unreleased line.
3. Run the grep-sweep list (one command per stale literal; paste outputs
   into the PR description).
4. Flip the eight ledger items → `fixed` (same PR).
5. Draft PR → pre-PR checklist (Doc/docstring-sync heading is the
   operative one — run it, don't skim it) → ready → merge.

## Validation Plan

```bash
rg -n -- '--topology-mode|--validate-output|--combine-only|--emit-selection' \
  --glob '!CHANGELOG.md' --glob '!.trellis/**'    # expect: only removed-flag history in CLAUDE.md
rg -n 'MEZMO_OTEL_STREAM_AUTH_SCHEME' README.md    # expect: 1+ row
uv lock --check
.venv/bin/python tools/check_copilot_instruction_contract.py
.venv/bin/pytest -m "not heavy" -n 2 && .venv/bin/pre-commit run --all-files
```

## Documentation And Spec Updates

This task *is* the doc update; additionally confirm
`.trellis/spec/amc/backend/` files do not repeat any fixed stale claim
(grep the same literals under `.trellis/spec/`).

## Review Notes

- Copilot will review this PR *using the instructions file it edits* —
  expect one round of self-referential comments; verify against HEAD
  before re-fixing (known cumulative-diff pattern).

## Follow-Ups

- A-059 (README scenario-table sync test) is deliberately in
  `07-17-audit-test-guard-lints`, not here — do not absorb it.
