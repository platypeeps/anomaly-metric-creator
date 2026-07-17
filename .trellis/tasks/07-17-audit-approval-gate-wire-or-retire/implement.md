# Approval-duplicate gate: wire or retire — Implementation Plan

## Execution Order

1. **Decision gate (one question at task start):** does the maintainer
   keep the duplicate-approval convention? Yes → A-lite; No → B. Design.md
   specifies both fully; record the answer in this task's PRD.
2. **A-lite path:** write `tools/pr_comment.sh` (pipe body → role-name
   lint → approval gate → `gh pr comment`); update CLAUDE.md's two chain
   snippets; add the convention to the canonical spec file; smoke the
   three transcript cases from design.md.
   **B path:** delete script + test file; grep-sweep
   `check_approval_duplicate` across the repo; CHANGELOG retirement line;
   collapse the CLAUDE.md section.
3. Flip A-034 → `fixed` in `.trellis/audit/ledger.md` (same PR).
4. Draft PR → checklist → ready → merge.

## Validation Plan

```bash
# A-lite:
echo "APPROVED test" > /tmp/body.md && bash tools/pr_comment.sh --dry-run … # transcript
.venv/bin/pytest tests/test_role_name_leaks_lint.py tests/test_approval_duplicate_lint.py -n 0
# B:
grep -rn "check_approval_duplicate" . --exclude-dir=.git   # CHANGELOG only
.venv/bin/pytest
```

## Documentation And Spec Updates

- CLAUDE.md (either path), spec file (A-lite), CHANGELOG (B).

## Review Notes

- If A-lite: the wrapper is operator tooling, not CI — say so, and keep it
  out of the workflow-pip / CI-mirror lint scopes.

## Follow-Ups

- Pack-level wiring (skills calling the wrapper) — only via a consented
  upstream pack PR; paste-ready handoff note if requested.
