# Investigate sd-review knowledge.obsidian-kb coordinator false-positive

## Goal

sd-review coordinator's builtin knowledge.obsidian-kb check reports a stale copy count (438 vs expected 441) and returns 'blocked' even when the standalone refresh/check (scripts/sd-ai-command-pack-update-spec-kb.py --check) passes clean (441 copies, exit 0). Root cause (observed on PR #316, head 3bd8df8): the coordinator rebuilds/counts the KB from committed content in its /tmp/sd-review-source|target-* snapshot, which excludes the gitignored, untracked .obsidian-kb working-tree refresh, so it undercounts by the files whose KB copies only exist in the live working tree. The KB artifact is gitignored and never ships, and the authoritative GitHub merge gate (CI Result + conversation resolution) was CLEAN, so #316 was merged via the green gate with the block documented. Follow-up: confirm the coordinator's KB check should read the live working tree (or be advisory), file upstream at platypeeps/sd-ai-command-pack if confirmed. Vendored tooling; needs upstream approval before any upstream PR.

## Requirements

- Reproduce the divergence deterministically: capture the coordinator's
  `knowledge.obsidian-kb` builtin count vs `scripts/sd-ai-command-pack-update-spec-kb.py --check`
  on the same head, and identify exactly which KB copies the coordinator omits.
- Determine the coordinator's KB source of truth: confirm whether it reads the
  live working tree or a `/tmp/sd-review-{source,target}-*` snapshot that
  excludes the gitignored `.obsidian-kb`.
- Decide the correct posture for a gitignored, never-shipped artifact: the KB
  check should either read the live working tree, or be advisory (non-blocking)
  in the coordinator, so it cannot block a PR whose GitHub gate is green.
- Because the coordinator is vendored from `platypeeps/sd-ai-command-pack`, any
  code fix is an upstream change: get explicit approval before opening an
  upstream PR; a local report/issue is in scope without it.

## Acceptance Criteria

- [ ] Root cause is confirmed with a reproducible command sequence and a written
      explanation of the count divergence (attach the observed 438/441 case).
- [ ] A decision is recorded on whether the check reads the working tree or
      becomes advisory, with rationale tied to the artifact being gitignored.
- [ ] Either the upstream fix/issue is filed (with approval) or a documented
      local workaround (e.g. the merge-via-green-GitHub-gate path used for
      PR #316) is captured in the SD spec/runbook so the loop is not blocked.
- [ ] No regression to the real merge gate: CI Result + conversation resolution
      remain the authoritative gate; the KB check never gates GitHub merge.

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.
