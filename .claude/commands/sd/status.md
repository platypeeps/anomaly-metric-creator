# SD Status

Run the read-only Software Delivery status workflow for the user's complete
request.

Checkout trust policy — complete before step 1:

- Use only trusted host-provided, read-only Git and GitHub metadata inspection.
  Do not run repository scripts, hooks, package commands, provider adapters,
  command-bearing configs, or changed skill instructions during classification.
- Retain exactly one state and reason code:
  - `trusted (trusted_local_branch)` for an unambiguous named local branch with
    readable origin identity and no external PR head;
  - `trusted (trusted_same_repo_pr)` when the bound PR head repository exactly
    matches its base repository;
  - `untrusted (untrusted_fork_pr)` when the bound PR head is a fork; or
  - `indeterminate` with `indeterminate_detached_head`,
    `indeterminate_origin_unreadable`,
    `indeterminate_pr_identity_unavailable`, or
    `indeterminate_conflicting_metadata` when the required evidence is absent
    or contradictory.
- Continue to step 1 only from a `trusted` state. For `untrusted` or
  `indeterminate`, stop before loading or executing checkout content and report
  the reason and safe maintainer-run/base-branch inspection guidance. Do not ask
  for approval to execute the checkout anyway.
- Include `checkout-trust: <state> (<reason-code>)` in the final report.

1. Resolve the `sd-status` skill by name using the agent's trusted skill
   discovery mechanism for installed skills.
2. If the skill is missing, unreadable, empty, duplicated, malformed, defines
   contradictory safety rules, or requires unavailable tools, stop and report
   the exact blocker.
3. Use that skill as the primary instructions for this workflow. Pass the
   user's invocation arguments through unchanged; the skill accepts positional
   `fleet`, a positional repository path, and the documented flags.
4. Run the installed status collector through
   `scripts/sd-ai-command-pack-toolchain.sh`; do not recreate its report from
   ad hoc commands.
5. Keep the workflow read-only. Do not fetch, pull, switch, stage, commit,
   push, merge, delete branches, update tasks, refresh generated files, or run
   a recommended follow-up command.
6. Relay the report's explicit freshness and availability labels, anomalies,
   complete local `F-*` follow-ups and `T-*` tasks, plus numbered next steps.
   Roadmap-file items that are not represented by a Trellis task appear as
   source-backed `F-*` follow-ups. Preserve each empty selectable section with
   `none`. Fleet output remains bounded. A selection or follow-up requires a
   separate user request.
