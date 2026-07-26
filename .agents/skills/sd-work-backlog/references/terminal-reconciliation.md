# Terminal Reconciliation

Load this reference only when work-loop status reports
`terminal_reconciliation` with this exact path. This is a historical audit,
not permission to revive the run.

Before changing the ledger, verify all of the following:

1. The recorded task is archived below `.trellis/tasks/archive/`; its regular,
   non-symlink `task.json` is `completed`, and its ID matches the ledger task.
2. `gh pr view` reports the delivery PR as `MERGED` with its exact URL, full
   head SHA, merge commit SHA, and default base branch. Verify the same complete
   group for a separate bookkeeping PR, if one exists.
3. Every submitted commit exists locally. The clean checked-out default branch
   tracks `origin/<default>`, and both tips equal the submitted observed head.
4. No live or ambiguous run owner exists. Recover a stale lock only after
   process liveness and repository evidence prove it safe.

For a pending historical reconciliation, invoke the local-only audit with the
preverified evidence:

```bash
bash scripts/sd-ai-command-pack-toolchain.sh run-python -- \
  scripts/sd-ai-command-pack-work-loop.py reconcile-terminal --repo . \
  --run-id <run-id> --archived-task <archive-path> \
  --delivery-pr-number <n> --delivery-pr-url <url> \
  --delivery-head <sha> --delivery-merge-commit <sha> \
  --bookkeeping-pr-number <n> --bookkeeping-pr-url <url> \
  --bookkeeping-head <sha> --bookkeeping-merge-commit <sha> \
  --branch <default> --head <default-head> --json
```

Omit the complete bookkeeping group when no bookkeeping PR exists. The helper
does no network work, preserves counters and historical `current` evidence,
treats an identical verified record as a byte-for-byte no-op, and rejects
contradictory evidence. A verified terminal record remains historical; a later
explicit backlog invocation may begin a new run.
