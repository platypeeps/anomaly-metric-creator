# Ownership Recovery

Load this reference only when work-loop status reports `owner_stale` or
`owner_invalid` with this exact path.

Concurrent loops for one repository are forbidden. An old timestamp alone is
not permission to steal a run.

1. Inspect the reported run ID, repository digest, host, PID, heartbeat, and
   whether the lock is the run lock or terminal-reconciliation lock.
2. For `owner_invalid`, preserve the unreadable lock bytes and stop unless an
   independent authoritative ledger plus process evidence makes ownership
   unambiguous.
3. For `owner_stale`, prove the prior process is gone and reconcile the ledger
   with live Trellis, Git, branch, and PR state before mutation.
4. Use `--recover-stale-lock` only after those checks. Never use it for an
   active owner or as a substitute for terminal reconciliation.
5. Re-run `status`; continue only when the typed recovery reason changes or is
   `normal`.
