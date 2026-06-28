---
name: trellis-housekeeping
description: Use after a pull request has merged to clean up the local development stream, prune stale refs, and report the expected clean repo state plus anomalies.
---

# Trellis Housekeeping

Run this project-local skill for `/trellis:housekeeping` style work after a PR
has merged and the user wants the repo back to a clean default-branch state.

The canonical implementation is:

```bash
bash scripts/trellis-housekeeping.sh
```

## Task List

The script performs this post-merge cleanup flow:

1. Verify the current repository, branch, and working-tree status.
2. Fetch and prune `origin` so local remote-tracking refs reflect GitHub.
3. Detect the remote default branch, usually `main`.
4. If the current branch is a feature branch, use `gh pr view` to confirm the
   branch's PR is `MERGED` and the local branch head matches the merged PR head
   before deleting anything.
5. When the current feature branch is confirmed merged and the working tree is
   clean, switch to the default branch and fast-forward it from `origin`.
6. Delete the merged local feature branch.
7. Delete the merged remote feature branch unless
   `--keep-remote-branch` is passed.
8. Verify the expected final state:
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
- `--keep-remote-branch`: delete the merged local branch but leave the remote
  branch on GitHub.
- `--remote <name>`: use a remote other than `origin`.

## Final Report

Report:

- Whether the repo reached the expected clean state.
- Which branch was cleaned up, if any.
- Any anomalies exactly as the script printed them.
- Whether follow-up manual action is needed.
