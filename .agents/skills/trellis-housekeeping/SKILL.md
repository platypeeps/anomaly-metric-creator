---
name: trellis-housekeeping
description: Use at the end of a development stream to finalize a ready PR when safe, clean up after merge, prune stale refs, and report the expected clean repo state plus anomalies.
---

# Trellis Housekeeping

Run this project-local skill for `/trellis:housekeeping` style work when the
user wants a ready PR finalized and merged, or after a PR has merged and the
repo should return to a clean default-branch state.

The canonical implementation is:

```bash
bash scripts/trellis-housekeeping.sh
```

## Task List

The script performs this end-of-stream flow:

1. Verify the current repository, branch, and working-tree status.
2. Fetch and prune `origin` so local remote-tracking refs reflect GitHub.
3. Detect the remote default branch, usually `main`.
4. If the current branch is a feature branch with an open PR, auto-finalize and
   merge only when all of these are true:
   - the working tree is clean
   - the local branch head, remote branch head, and PR head are identical
   - the PR is open, not draft, targets the default branch, and has a `CLEAN`
     merge state
   - the PR has at least one reported check and every reported check is green
   - GitHub review threads have no unresolved comments
   - `trellis-finalize` is available on `PATH` and exits successfully
5. After `trellis-finalize`, push the journal commit back to the PR branch.
   By default, the script amends that finalize commit with `[skip ci]` before
   pushing to avoid rerunning CI where GitHub branch protection allows it.
6. Merge the PR with `gh pr merge --match-head-commit`. If GitHub refuses the
   merge, usually because skipped required checks are pending, report an
   anomaly instead of forcing the merge.
7. If the current branch is a feature branch, use `gh pr view` to confirm the
   branch's PR is `MERGED` and the local branch head matches the merged PR head
   before deleting anything.
8. When the current feature branch is confirmed merged and the working tree is
   clean, switch to the default branch and fast-forward it from `origin`.
9. Delete the merged local feature branch.
10. Delete the merged remote feature branch unless
   `--keep-remote-branch` is passed.
11. Verify the expected final state:
   - default branch checked out
   - working tree clean
   - default branch matches `origin/default`
   - no extra local branches
   - no extra remote-tracking branches besides `origin/HEAD` and
     `origin/default`
   - no open PRs
   - no open issues
   - no active Trellis tasks assigned to the current developer

## Expected Output

A clean run should condense to:

```text
==> Expected clean state
- branch: main
- working tree: clean
- main matches origin/main
- local branches: only main
- remote branches: only origin/HEAD and origin/main
- open PRs: none
- open issues: none
- Trellis active tasks: none

==> Anomalies
none
```

If anything differs from that expected state, the script prints the clean items
that still hold and then lists anomalies. Treat the anomaly list as the handoff:
it should be short enough to read quickly and specific enough to decide the next
manual action.

## Safety Rules

- Never delete a non-default branch unless GitHub confirms that branch's PR is
  `MERGED` and the local branch head matches the merged PR head.
- Never auto-finalize or merge unless the open PR is green, comment-clean,
  mergeable, and exactly matches the current local and remote branch heads.
- Never force a merge. If `[skip ci]` leaves required checks pending, report the
  blocked merge as an anomaly and let the user rerun without CI suppression.
- Never switch branches or delete branches when the working tree is dirty.
- If the current branch has an open PR, no PR, or inaccessible PR metadata,
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
- `--no-auto-finalize`: skip the pre-merge finalize/merge gate and only run
  post-merge cleanup.
- `--run-finalize-ci`: do not amend the `trellis-finalize` commit with
  `[skip ci]`.
- `--merge-strategy <merge|squash|rebase>`: choose the strategy for an
  auto-finalized PR. Defaults to `merge`.
- `--keep-remote-branch`: delete the merged local branch but leave the remote
  branch on GitHub.
- `--remote <name>`: use a remote other than `origin`.

## Final Report

Report:

- Whether the repo reached the expected clean state.
- Whether a ready open PR was finalized and merged, or why it was skipped.
- Which branch was cleaned up, if any.
- Any anomalies exactly as the script printed them.
- Whether follow-up manual action is needed.
