# Stopped Or Red Run Recovery

Load this reference only when work-loop status reports `run_stopped` or
`context_red` with this exact path.

Reload the current Trellis task, applicable specs, branch, full HEAD, working
tree, upstream, PR, and every non-null `current` field from status. Do not
repeat a side effect until the ledger and live state agree.

- A checkpoint overlays its owning lifecycle phase. Preserve its human target
  and `checkpoint.resumePhase`.
- Supply every recorded non-null current-state field to `reconcile`. Omit only
  fields that status reports as null.
- When live evidence proves a later lifecycle phase, pass the exact observed
  phase and values with `--verified-live-advance`; repeat exact reconciliation
  without that flag to move amber agreement to green.
- A schema-v1 ledger whose phase is literally `checkpoint` uses a phase-valued
  target as owner. If its target is human text, supply
  `--resume-phase <recorded-lifecycle-phase>` only when independently proven.
- If the ledger claims work live state cannot prove, preserve red health and
  stop. Never reconstruct missing evidence from conversation history.

```bash
bash scripts/sd-ai-command-pack-toolchain.sh run-python -- \
  scripts/sd-ai-command-pack-work-loop.py reconcile --repo . \
  --run-id <run-id> --observed-phase <observed-lifecycle-phase> \
  --task <task> --branch <branch> --head <sha> \
  --base-branch <base-branch> --pr-number <n> --pr-url <url> \
  --last-shipped-sha <sha> --verified-live-advance --json
```

After reconciliation, re-run `status` and follow only its next typed recovery
directive. A stopped run with externally merged/archive evidence may advance to
`terminal_reconciliation`; do not choose that path yourself.
