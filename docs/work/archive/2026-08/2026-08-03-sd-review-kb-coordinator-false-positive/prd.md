---
title: Investigate sd-review knowledge.obsidian-kb coordinator false-positive
status: done
created: 2026-08-03
branch: codex/sd-review-kb-false-block-runbook
---
# Investigate sd-review knowledge.obsidian-kb coordinator false-positive

## Goal

sd-review coordinator's builtin knowledge.obsidian-kb check reports a stale copy count (438 vs expected 441) and returns 'blocked' even when the standalone refresh/check (scripts/sd-ai-command-pack-update-spec-kb.py --check) passes clean (441 copies, exit 0). Root cause (observed on PR #316, head 3bd8df8): the coordinator rebuilds/counts the KB from committed content in its /tmp/sd-review-{source,target}-* snapshot, which excludes the gitignored, untracked .obsidian-kb working-tree refresh, so it undercounts by the files whose KB copies only exist in the live working tree. The KB artifact is gitignored and never ships, and the authoritative GitHub merge gate (CI Result + conversation resolution) was CLEAN, so #316 was merged via the green gate with the block documented. Follow-up: confirm the coordinator's KB check should read the live working tree (or be advisory), file upstream at platypeeps/sd-ai-command-pack if confirmed. Vendored tooling; needs upstream approval before any upstream PR.

**CORRECTION (confirmed in `research/root-cause.md`):** the original `/tmp`-snapshot
undercount hypothesis above is **refuted**. `_run_check` builds no snapshot; the
coordinator's KB row reads the same live working tree the standalone `--check`
does. The real mechanism is that `.obsidian-kb` is a symlink to a **live external
Obsidian vault** (gitignored, untracked, mutating independent of HEAD), so the
`--check` fails non-deterministically (present != expected mid-edit) and the
coordinator memoizes that transient failure against a state key that excludes the
gitignored artifact. The specific "438/441" figures were the PR #316 observation
under the refuted framing; the confirmed evidence is the non-deterministic count
swing recorded in `research/root-cause.md`.

## Requirements

- Characterize the divergence: capture the coordinator's
  `knowledge.obsidian-kb` verdict vs `scripts/sd-ai-command-pack-update-spec-kb.py --check`
  on the same head. (Confirmed non-deterministic, not a fixed undercount — the
  coordinator omits *no* copies; it reads the same live working tree the
  standalone `--check` does, and the count swings with the external vault. See
  `research/root-cause.md`.)
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

- [x] Root cause is confirmed with a reproducible command sequence and a written
      explanation of the count divergence. (The specific 438/441 case was the
      PR #316 observation under the refuted undercount framing; the confirmed
      evidence is the non-deterministic count swing + the `_run_check`/
      `kb_freshness_row`/`check_current` code trace in `research/root-cause.md`.)
- [x] A decision is recorded on whether the check reads the working tree or
      becomes advisory, with rationale tied to the artifact being gitignored.
- [x] Either the upstream fix/issue is filed (with approval) or a documented
      local workaround (e.g. the merge-via-green-GitHub-gate path used for
      PR #316) is captured in the SD spec/runbook so the loop is not blocked.
- [x] No regression to the real merge gate: CI Result + conversation resolution
      remain the authoritative gate; the KB check never gates GitHub merge.

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.

## References

Research notes that lived beside this item's Trellis record and were not carried
into docs/work. Recover the bodies from git history under `.trellis/tasks/archive/2026-08/08-03-sd-review-kb-coordinator-false-positive`:

- research/root-cause.md
