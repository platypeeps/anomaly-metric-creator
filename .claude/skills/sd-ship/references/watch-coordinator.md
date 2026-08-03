# Watch Coordinator

Internal read-only wait procedure for a published PR's required checks and
review threads. It is not a command: it has no adapter, no catalog row, and no
direct user invocation. Consumers are `sd-ship` Stage 3 and `sd-fleet-refresh`
`merge-eligibility`. The coordinator reports sequencing advice only; it never
merges, never mutates local or remote state, and never hands off to
housekeeping. The merge decision's authoritative read is always the
housekeeping eligibility evaluator's own atomic recomputation, so a state
change after the last probe cannot cause a wrong merge — only a housekeeping
stop with its report.

## Probe

One probe is one invocation of the existing read-only dependency-PR mode of
the eligibility script, in full:

```bash
bash scripts/sd-ai-command-pack-toolchain.sh run-python -- \
  scripts/sd-ai-command-pack-pr-eligibility.py \
  --repo . --dependency-pr-number <N> --remote origin \
  --default-branch <base> --github-repository <owner>/<repo>
```

`<N>` is the watched PR number, `<base>` the PR's base branch, and
`<owner>/<repo>` the GitHub repository. Parse the JSON result; do not
classify from the exit code alone. Thread evidence comes from the script's
own bounded fail-closed pager; do not add a second pagination path or any
supplementary thread query.

## Polling Loop

- Interval: 20 seconds between probes.
- Attempt ceiling: `timeout-minutes × 3` probes (default `timeout-minutes`
  is 30, so 90 attempts). Stop at the ceiling regardless of state.
- Classify each probe result in this order:
  1. **Probe failure** — the invocation exits with an unexpected error, the
     output is unparseable, or the result is a non-retryable indeterminate
     (`retryable` false, including `invalid_result`). Stop immediately and
     report `probe-failed` with the probe's diagnostic; do not keep polling
     past a non-retryable result.
  2. **Retryable indeterminate** — `status` is indeterminate and `retryable`
     is true (for example transient thread-listing unavailability). Keep
     polling within the ceiling.
  3. **Pending checks** — classification keys on `checks.items`, not on
     reason codes: any item with `CheckRun.status != "COMPLETED"` or
     `StatusContext.state == "PENDING"` means checks are still running.
     Keep polling within the ceiling.
  4. **Settled** — no pending items. `status: "eligible"` reports
     `settled-green`; a blocked result reports `settled-blocked` with the
     probe's reason codes.

## Outcomes

Exactly four reportable outcomes, never an exception and never a silent
not-ready:

- `settled-green` — checks completed and the eligibility probe reports
  eligible. The only outcome that advises proceeding to the next stage.
- `settled-blocked` — checks completed but the probe reports blocking
  reasons (failed checks, unresolved threads, merge-state problems). Relay
  the probe's reason codes and evidence.
- `timed-out` — the attempt ceiling elapsed while checks were still pending
  or retryable-indeterminate. Relay the last probe's state.
- `probe-failed` — a probe failure per rule 1 above. Relay the diagnostic.

Evidence limits, by construction of the probe: a merge-state-blocked result
short-circuits before thread listing, so `settled-blocked` reports may carry
no thread evidence; complete review-thread evidence is guaranteed only on
`settled-green`. Consumers must not treat an absent thread list in a blocked
report as "no threads".
