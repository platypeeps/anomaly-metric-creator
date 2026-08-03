# Ledger Recovery

Load this reference only when work-loop status reports `ledger_missing` or
`ledger_invalid` with this exact path.

`ledger_missing` means an owner lock exists without its authoritative state
file. `ledger_invalid` means the state file cannot be parsed or validated.
Neither condition authorizes creating a replacement ledger or replaying a side
effect.

1. Record the reported reason, state-root path, and non-mutating Git/Trellis/PR
   observations.
2. Inspect the user-local state directory without copying state into the repo.
3. If a known prior state root contains one unambiguous ledger for the same
   repository digest, preserve the original bytes and restore it atomically.
4. If no authoritative ledger can be proved, persist a repository-wide blocker
   outside the missing ledger and stop. Do not infer counters, phase, task,
   owner, or PR evidence from chat history.
5. Re-run `status`; continue only when it returns a different typed recovery
   reason or `normal`.

Never delete a lock merely because the ledger is absent. Ownership recovery is
separate and must be selected by the helper after a readable ledger exists.
