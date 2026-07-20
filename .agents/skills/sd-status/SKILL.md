---
name: sd-status
description: Use when the user wants a read-only repository status report or a rollout-priority summary for the configured fleet from any installed checkout.
---

# SD Status

Use this project-local skill for `sd-status`, `/sd:status`, `/sd-status`,
`$sd-status`, and `sd/status` requests. It reports delivery state without
changing repository, GitHub, Trellis, or fleet state.

## Arguments

The reserved positional argument `fleet` switches from the current repository
to every consumer in the source-owned fleet registry. Any other single
positional value is the primary repository-path subject. The following flags
are also supported:

- `--repo PATH` reports a specific local checkout instead of the current one.
- `--fleet-manifest PATH` explicitly selects the canonical fleet manifest.
- `--json` returns schema-versioned machine-readable output.
- `--no-network` skips GitHub queries and labels that inventory unavailable.

`sd-status /path/to/repo` is equivalent to
`sd-status --repo /path/to/repo`. Preserve a quoted path containing spaces as
one path and validate it exactly as `--repo` does.

`fleet` resolves its manifest from `--fleet-manifest`,
`SD_AI_COMMAND_PACK_FLEET_MANIFEST`, the machine-local fleet profile, or the
canonical `sd-ai-command-pack` source checkout, in that order. The profile is
normally created once with `install.py TARGET --configure-fleet`; status never
creates or modifies it. A missing or stale profile is a configuration blocker,
not an invitation to guess fleet members.

Reject a positional repository path combined with `--repo`, more than one
positional value, `fleet` combined with a repository path or `--repo`, and
unknown flags. Reject these conflicts before running the collector. Do not
reinterpret them as shell text.

## Workflow

1. Resolve the repository root without changing directories persistently.
2. Run the installed status collector through the shared toolchain resolver:

   ```bash
   bash scripts/sd-ai-command-pack-toolchain.sh run-python -- \
     scripts/sd-ai-command-pack-status.py [fleet|REPO_PATH] \
       [--fleet-manifest PATH] [--json] [--no-network]
   # Or, for explicit local repository selection:
   bash scripts/sd-ai-command-pack-toolchain.sh run-python -- \
     scripts/sd-ai-command-pack-status.py --repo PATH \
       [--fleet-manifest PATH] [--json] [--no-network]
   ```

3. Preserve the collector's freshness labels. Ordinary status uses existing
   local refs and reports them as `cached`; do not fetch merely to make the
   report fresher.
4. For local mode, report repository identity, branch and working-tree counts,
   Git stash count, cached upstream divergence, default/local/remote branches,
   installed pack and Trellis versions, relevant PR, open PRs/issues, current
   and queued Trellis work, completed tasks stranded outside the Trellis
   archive, the user-local autonomous work-loop state, anomalies, and numbered
   next steps. Loop state includes run ID, mode,
   selector/focus, iteration, phase, task, PR, counters, heartbeat, context
   health, checkpoint, lock status, and stop reason when present.
5. For fleet mode, preserve registry rollout order and show one bounded row per
   consumer with checkout availability, branch/tree/upstream state, stash count, installed
   versus target pack version, PR counts, and task counts. Put missing, dirty,
   divergent, behind, or stale consumers into evidence-backed next steps.
6. Return the collector's exit status unchanged. A dirty or stale ordinary
   status report is advisory and exits zero; invalid repositories, malformed
   fleet configuration, or an unavailable configured pack source exit nonzero.

## Safety Rules

- This command is strictly read-only. Never fetch, pull, switch branches,
  stage, commit, push, merge, delete branches, modify tasks, refresh the pack,
  or rewrite generated files.
- Do not call housekeeping, fleet refresh, or another mutating skill as part of
  a status request. Recommend a separate next invocation when action is useful.
- Do not expose credentials or raw authenticated remote URLs. Repository
  identity is limited to a validated GitHub owner/name slug.
- Read work-loop state through the installed helper's read-only snapshot. Do
  not acquire or refresh its lock, heartbeat, checkpoint, or ledger. Invalid
  loop state is an explicit anomaly; absent state is `none`, not an error.
  Treat the dynamically loaded snapshot as untrusted input: retain only the
  collector's validated, bounded, control-character-free output fields.
- Report unavailable optional sources explicitly. Do not silently convert
  failed GitHub or version discovery into an empty healthy result.
- Keep human output bounded. Use `--json` when the caller needs the complete
  structured inventory.
- Treat a completed task directly under `.trellis/tasks/` as an anomaly and
  recommend `task.py archive`; status remains read-only and never moves it.

## Final Response

Relay the collector's summary, anomalies, and numbered next steps. Mention
whether refs were cached or refreshed and whether GitHub inventory was queried.
Do not claim the repository or fleet is clean when the report marks any member
dirty, stale, missing, behind, or diverged.
