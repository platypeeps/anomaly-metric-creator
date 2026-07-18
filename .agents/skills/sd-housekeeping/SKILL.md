---
name: sd-housekeeping
description: Use at the end of a development stream to run finish-work before merging a ready PR, clean up after merge, prune stale refs, and report the expected clean repo state plus anomalies.
---

# SD Housekeeping

Run this project-local skill for `sd-housekeeping` and `/sd:housekeeping` style
work when the user wants a ready PR wrapped up and merged, or after a PR has
merged and the repo should return to a clean default-branch state.

This command is intentionally narrow: use it only for the current repository's
active development stream or its just-merged PR cleanup. It is not a general
repo maintenance, dependency upgrade, issue triage, or branch-pruning command.
If the user asks for broader cleanup, stop and clarify the intended scope before
running the housekeeping script.

Run from the feature branch when the command may merge a ready open PR. If the
PR is already merged and cleanup starts from the default branch, the script only
performs post-merge/default-branch cleanup and inventory reporting.

The canonical implementation is:

```bash
bash scripts/sd-ai-command-pack-housekeeping.sh
```

## Task List

This command performs this end-of-stream flow:

1. Verify the current repository, branch, and working-tree status.
2. If the current branch is a feature branch with an open PR that is not yet
   merged, run the SD finish-work flow before actual housekeeping:
   - execute the `sd-finish-work` flow, which prepares task records,
     validation notes, and handoff state for this work stream
   - if finish-work creates archive or journal commits, push the current
     branch before continuing, then refresh PR/check state for the new HEAD and
     continue only after required checks are complete and green
   - if finish-work reports uncommitted PR work or ambiguous dirty files, stop
     and report that blocker instead of running cleanup
3. The script fetches and prunes `origin` so local remote-tracking refs reflect
   GitHub.
4. The script detects the remote default branch, usually `main`.
5. If the current branch is a feature branch with an open PR, the script merges
   only when all of these are true:
   - the working tree is clean
   - the local branch head, remote branch head, and PR head are identical
   - the PR is open, not draft, targets the default branch, and has a `CLEAN`
     merge state
   - at least one executed check succeeded, and no check is blocking: pending,
     or any conclusion other than success, skipped, or neutral (for example
     failed, cancelled, or timed out). Checks skipped by change classifiers do
     not block the merge.
   - GitHub review threads have no unresolved comments
6. The script merges the PR with `gh pr merge --match-head-commit`. If GitHub
   refuses the merge, report an anomaly instead of forcing the merge.
7. If the current branch is a feature branch, use `gh pr view` to confirm the
   branch's PR is `MERGED` and the local branch head matches the merged PR head
   before deleting anything.
8. When the current feature branch is confirmed merged and the working tree is
   clean, switch to the default branch and fast-forward it from `origin`.
9. Delete the merged local feature branch.
10. Delete the merged remote feature branch unless
   `--keep-remote-branch` is passed.
11. The housekeeping script delegates final verification to the installed
    `sd-status` collector in strict mode. It passes the default/source branch,
    remote-branch policy, whether refs were refreshed, and every cleanup
    anomaly; do not run a parallel final-state collector.
12. Treat the delegated status report as authoritative for the expected clean
    state, pack/Trellis versions, relevant PR and review rounds, repo-wide open
    PRs/issues, Trellis inventory, anomalies, and numbered next steps. Inventory
    alone does not block current-stream cleanup.
13. Preserve session-only follow-up context in the concise chat summary when it
    is not observable from repository state, but do not replace or contradict
    the status report's evidence-backed next steps.

## Expected Output

A clean current-stream cleanup ends with the shared status report:

```text
SD status: healthy
Ref freshness: refreshed

==> Expected clean state
- branch: <default>
- working tree: clean
- upstream: origin/<default>; synchronized
- local branches (1): <default>
- remote source branch absent: origin/<feature>

==> Delivery
- SD pack: <installed version>
- Trellis: <installed version>
- relevant PR: #<number> MERGED

==> Inventory
- open PRs (<count>): <summary>
- open issues (<count>): <summary>
- current Trellis task: <summary>

==> Anomalies
none

==> Next Steps
1. <highest-value evidence-backed next action>
```

If current-stream cleanup differs from that expected state, the script prints
the clean items that still hold and then lists anomalies. Treat the anomaly
list as the handoff: it should be short enough to read quickly and specific
enough to decide the next manual action. Repo-wide inventory lines are context
for the operator; they do not by themselves mean this housekeeping run failed.

The assistant's final response should be shorter than the raw script output and
use this shape for a clean run:

```text
Housekeeping completed cleanly.
PR #<number> was <merged by housekeeping|already merged by the time the script ran>; housekeeping confirmed the merge, switched to <default>, fast-forwarded to origin/<default>, deleted the local and remote <feature> branch, and pruned refs.

Final state:
Branch: <default>
Working tree: clean
<default> matches origin/<default>
Local branches: only <default>
Remote branches: origin/HEAD, origin/<default>
PR #<number>: merged at <timestamp>
Open PRs: <none|summary>
Open issues: <none|summary>
Current Trellis task: <none active|task id + status>
PR review rounds: <n submitted reviewer review(s)|n/a — no PR in this run>
Anomalies: none

Insight:
<One short evidence-backed observation about what housekeeping proved or surfaced; omit this section when there is nothing useful beyond the final state.>

Next Steps:
<Always present, even on a verification-only clean run. A short numbered list covering, in order: open follow-up items discovered this session, any in-progress Trellis task to resume, then the next high-value Trellis task candidates / roadmap items to tackle (planning tasks assigned to the current developer first, then other high-value repo-local tasks). If the backlog is genuinely empty, write "No open or planned Trellis work — backlog is clear." Never omit this section.>
```

If cleanup is not clean, keep the same top-level shape, change the first line
to the clearest status sentence, set any unknown or failed final-state rows to
the exact script result, and put the exact anomaly summary in `Anomalies:`.
Include an `Insight:` section only when the script output or session context
supports a useful observation, such as the PR lifecycle being healthy, cleanup
being verification-only because the PR was already merged, stale refs being
pruned, the repo being ready for the next work stream, or a process improvement
being worth tracking. Do not add filler insights that merely restate `clean`.
Always end with the numbered `Next Steps` section delegated by status, even
when the current stream needs no follow-up. Also state the current task and review-cycle
cost from the status report in the final-state rows. Do not rerun `gh`, Trellis,
or Git commands solely to reconstruct those values. Do not include speculative
work: if a category has no evidence, say so plainly, and if the whole backlog is
empty, write that the backlog is clear rather than dropping the section.

## Safety Rules

- Never delete a non-default branch unless GitHub confirms that branch's PR is
  `MERGED` and the local branch head matches the merged PR head.
- Never merge a ready open PR from the command flow before finish-work has
  completed and any finish-work commits have been pushed.
- Never auto-merge unless the open PR is green, comment-clean, mergeable, and
  exactly matches the current local and remote branch heads.
- Never force a merge. If branch protection blocks the merge, report the
  blocked merge as an anomaly.
- Never switch branches or delete branches when the working tree is dirty.
- If the current branch has a closed PR, no PR, or inaccessible PR metadata,
  leave it alone and report an anomaly.
- Do not stage, commit, or push unrelated work as part of housekeeping.
- Use `--dry-run` when the user wants a preview before any mutating git
  command, including fetch, pull, branch switching, or branch deletion. Dry-run
  output records that final git-state verification was skipped because the repo
  was not changed.
- If the script exits nonzero, report the anomalies instead of retrying with
  stronger deletion commands.

## Options

- `--dry-run`: show what would be cleaned up without running mutating git
  commands such as fetch, pull, branch switching, or branch deletion.
- `--no-auto-merge`: skip the ready-open-PR merge gate and only run post-merge
  cleanup.
- `--merge-strategy <merge|squash|rebase>`: choose the strategy for an
  auto-merged PR. Defaults to `merge`.
- `--keep-remote-branch`: delete the merged local branch but leave the remote
  branch on GitHub.
- `--remote <name>`: use a remote other than `origin`.
- `--self-test`: verify the installed script's merge-gate contract against
  stubbed scenarios and exit. Hermetic (no git, gh, or network access), so
  consumer repos can run it from CI instead of maintaining bespoke contract
  tests over the vendored script.

## Final Report

Report:

- Whether the repo reached the expected clean state.
- Whether finish-work ran or blocked the command.
- Whether a ready open PR was merged, or why it was skipped.
- Which branch was cleaned up, if any.
- Any anomalies exactly as the delegated status report printed them.
- Any brief evidence-backed insight from the cleanup, only when it adds signal
  beyond the final-state rows.
- Whether follow-up manual action is needed.
- The current Trellis task (its id + status, or `none active`).
- PR review rounds for the merged/confirmed PR as reported by status, or
  `unavailable` when the optional GitHub query failed.
- A final numbered `Next Steps` section — always present, including on a
  verification-only clean run — covering, in order: open follow-up items
  discovered during this session, existing Trellis tasks that are already
  `in_progress`, and high-value Trellis task candidates / roadmap items to
  tackle. Say the backlog is clear only when it genuinely has no open or
  planned work; never omit this section.
