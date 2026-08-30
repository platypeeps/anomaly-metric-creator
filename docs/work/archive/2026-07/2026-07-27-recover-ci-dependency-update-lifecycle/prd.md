---
title: Recover CI dependency update lifecycle
status: done
created: 2026-07-27
branch: codex/repair-ci-dependency-updates
---
# Recover CI dependency update lifecycle

## Goal

Produce valid Trellis finish-work evidence for the already implemented and
reviewed CI dependency update stream so PR #307 can be merged through guarded
housekeeping without changing its reviewed implementation.

## Background

- PR #307 consolidates superseded GitHub Actions dependency PRs #302-#305.
- Exact implementation head `b8a62e84d51a69dafc79d7f4277c95e801518eb0`
  passed local checks, GitHub CI, and two Copilot review rounds with no
  unresolved threads.
- A no-task journal commit (`a8591f1`) is intentionally preserved, but its
  journal-only planning receipt was invalid because the referenced commits
  changed implementation paths rather than an active planning task.

## Requirements

- Preserve the reviewed implementation commits and existing journal commit;
  do not amend, reset, drop, or rewrite them.
- Limit recovery changes to this Trellis task, its generated `.obsidian-kb`
  copies, and the developer journal/index.
- Record the current feature branch and `main` as the PR target.
- Complete the canonical active-task archive and session-journal lifecycle.
- Generate a schema-version-1 `final-bundle --mode completion` receipt bound to
  the exact pushed head before housekeeping is allowed to merge.
- Keep CI green and require zero unresolved GitHub review threads on the final
  head.

## Acceptance Criteria

- [ ] The task is valid, scoped to lifecycle recovery, and reaches
  `in_progress` before finalization.
- [ ] The pre-archive validator returns `pre_archive_valid` for this exact task.
- [ ] The task is archived and a complete recovery journal session is recorded.
- [ ] The completion receipt is valid and its evidence head matches local HEAD.
- [ ] PR #307 is pushed, green, thread-clean, and merged only through
  `sd-housekeeping`.
- [ ] Superseded PRs #302-#305 are closed with an evidence-backed reference to
  PR #307, while unrelated PRs remain untouched.

## Out of Scope

- Further implementation, dependency, workflow, contract, or specification
  changes.
- PR #300 (Ruff pre-commit) and PR #306 (command-pack refresh).

## Notes

- This is a lightweight recovery task and is intentionally PRD-only.
