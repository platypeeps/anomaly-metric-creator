# Trace-export hardening — Implementation Plan

## Execution Order

1. Branch from `main`. A-018: `_neutralize_csv_cell` helper (idempotent;
   OWASP CSV-injection trigger set — a leading `=`, `+`, `-`, `@`, tab, or
   CR) applied to **every** cell `write_trace_bundle_csv`
   writes (enumeration-proof; not a named subset — see design.md for why
   the subset allowlist was rejected); tests per trigger-char × column
   matrix, plus benign-cell and idempotency cases. Check the debug UI for client-side
   CSV building; file a follow-up chip if found, do not widen.
2. A-019: `serve_main` + `start_test_server` gate on `*`-without-auth
   (grep suite callers first); parser-error wording per design; tests
   for the three flag combinations; CHANGELOG + SECURITY.md.
3. A-070: policy sentence in the version-mismatch error + code comment
   (bump-PR owns any future adapter); README trace-bundle policy
   paragraph.
4. Flip A-018/A-019/A-070 → `fixed` (same PR).
5. Draft PR → checklist (security heading — this is user-input-handling
   and export-surface work) → ready → merge.

## Validation Plan

```bash
.venv/bin/pytest tests/test_trace_bundle.py tests/test_server.py -n 0
.venv/bin/pytest && .venv/bin/pre-commit run --all-files
```

Manual: export a bundle containing a recorded `=2+5|' /C calc'!A0`
command via the live flow; open the CSV in a spreadsheet locally and
confirm inert text.

## Documentation And Spec Updates

- SECURITY.md: CSV-injection posture + CORS `*` rule.
- README: trace-bundle version policy; CHANGELOG: both behavior changes.

## Review Notes

- The `-` prefix corner (negative-number-looking free text) is the one
  reviewers will question — pre-empt with the correctness-over-cosmetics
  rationale from design.md.

## Follow-Ups

- Debug-UI client-side CSV exports (if the check in step 1 finds any).
