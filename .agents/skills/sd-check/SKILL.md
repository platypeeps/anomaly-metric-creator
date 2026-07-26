---
name: sd-check
description: Use when the user asks to run deterministic repository verification, obtain a typed check result, or verify readiness without AI review or repository mutation.
---

# SD Check

Run this project-local skill for `sd-check`, `/sd:check`, `/sd-check`,
`$sd-check`, and `sd/check` requests. It runs deterministic verification and
returns one versioned result without changing repository, Git, GitHub, generated
knowledge, or review-provider state.

## Workflow

1. Resolve the repository root and verify these installed sibling files exist:
   `scripts/sd-ai-command-pack-toolchain.sh`,
   `scripts/sd-ai-command-pack-check.py`, and
   `scripts/sd_ai_command_pack_lib.py`. If any is missing, stop and recommend
   reinstalling or refreshing the command pack; do not improvise a replacement.
2. Run the coordinator exactly once through the selected Python toolchain:

   ```bash
   bash scripts/sd-ai-command-pack-toolchain.sh run-python -- \
     scripts/sd-ai-command-pack-check.py --json
   ```

3. Parse the single schema-version-1 JSON document. Reject an unknown schema,
   malformed result, aggregate/exit contradiction, unknown status, duplicate
   row ID, or missing state-guard evidence rather than reconstructing a verdict
   from command output.
4. Return the coordinator's exit status unchanged. Do not rerun a failed row
   automatically and do not invoke the legacy full-check as a fallback.

## Repository Check Configuration

Repository-specific prerequisites and checks live only in
`.sd-ai-command-pack/check.json` schema version 1. Each entry has a safe unique
`id`, non-empty `argv` array, repository-contained `cwd`, and bounded positive
`timeoutSeconds`. The coordinator validates the complete file before executing
an entry. It rejects shell strings, inline shell/code commands, provider or
GitHub review executables, non-read-only Git operations, path escapes, unknown
fields/schema versions, and unbounded values.

Missing configuration is valid and runs the built-in inventory. A declared
missing executable is `unavailable`, not a successful skip. A prerequisite
that does not pass blocks later configured checks visibly.

## Result Contract

The coordinator distinguishes `passed`, `failed`, `skipped`, `unavailable`,
`invalid`, and `indeterminate`. The result includes ordered rows, bounded
diagnostics and remediations, counts, aggregate status, exact HEAD observation,
and before/after state-guard evidence. Human output is derived from the same
fields.

- Exit `0`: aggregate `passed` and the state guard passed.
- Exit `1`: a deterministic check failed or repository/Git state changed.
- Exit `2`: repository or configuration input is invalid.
- Exit `3`: required evidence or execution is unavailable/indeterminate.

## Safety Rules

- This command is strictly read-only. Never refresh `.obsidian-kb`, repository
  maps, generated adapters, manifest/provenance state, or other generated
  output. Relay the owning remediation command as a separate next action.
- Never invoke Prism, Gito, Copilot, a routed reviewer, `gh` review mutation,
  or another AI/provider lane. Review belongs to `sd-review` and its temporary
  predecessor workflows.
- Never fix, stage, commit, push, switch, merge, resolve threads, or update
  Trellis task state as part of this command.
- Every subprocess uses the shared external cache environment. Do not redirect
  authentication configuration or retry a cache-setup failure with bare tools.
- A state-guard failure is evidence of a non-read-only configured check. Report
  the changed state class and stop; do not revert or delete output implicitly.
- Do not call `sd-full-check` or read its environment/package-hook contract.
  That independent legacy surface remains only until its retirement task.

## Final Response

Report the aggregate status and exit code, then list every non-passing row with
its status, diagnostic, and remediation. Include passed/skipped counts, the
state-guard status, and whether repository configuration was present. Say the
check passed only when the typed aggregate and state guard both say `passed`.
