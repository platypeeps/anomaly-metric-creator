# Environment-blocked recovery evidence

> The single shape a workflow uses to say "an environment or authority boundary
> stopped me — not a repository defect" and to name the smallest safe retry
> point. Built only by an owning command from its own control flow, never from
> parsed stderr. Emitted by session recording, finish-work, housekeeping,
> work-loop persistence, knowledge-base refresh, and toolchain cache setup;
> composed and validated by `sd_ai_command_pack_lib`.

## What the fragment says

A blocked owner emits one `environment_blocked` fragment (schemaVersion `1`):

- `boundary` — which environment surface refused the write, from a fixed enum
  (see below). Not a free-form string.
- `operation` — the bounded owning operation that was attempted.
- `checkpoint` — the last verified point reached before the block, so a retry
  resumes from there instead of restarting the lifecycle.
- `mutationState` — `none`, `partial-recoverable`, or `unknown` (see *Retry*).
- `retryable` — a boolean the owner sets only when it can prove the checkpoint
  and idempotency conditions. `true` is never paired with `mutationState:
  unknown`.
- `recoveryAction` — bounded, redacted **data**: `null`, an `argv` token list,
  or a `skill` instruction string. It carries no executable authority.
- `diagnostic` — a bounded, secret-safe line. Secrets and any credentials
  embedded in URLs are stripped, and absolute filesystem paths are rendered as
  `[path]`; plain remote URLs and error descriptions (for example "Permission
  denied") are preserved as diagnostic context.

## The boundaries

- `git-metadata` — fetch, prune, or branch-ref writes on the local or remote
  Git metadata.
- `user-state` — user-local private state (work-loop persistence, caches under
  the user profile).
- `tool-cache` — the owned task-local toolchain cache setup.
- `kb-target` — the linked knowledge-base copy the spec refresh regenerates.
- `managed-payload` — installed managed pack files. (Reserved boundary: enum is
  supported; no producer emits it yet.)

Unknown failures are **not** guessed into this contract. A command that cannot
prove the boundary keeps its existing failure result and exit code.

## Retry is governed by mutationState

- `none` — nothing was written; the single bounded operation may be re-run as
  soon as the boundary is cleared.
- `partial-recoverable` — a regenerable or multi-step write was interrupted
  (e.g. a mirror refresh, a multi-ref prune). Reconcile from the named
  `checkpoint`; the operation is idempotent by design, so a clean re-run
  converges without duplicating work.
- `unknown` — the mutation extent is not provable. Never auto-retry. Surface it
  for a human decision.

## What a consuming skill must do

1. **Report the exact boundary and checkpoint** as given. Do not relabel a
   boundary or invent a broader cause.
2. **Request only the narrow authority** the bounded retry needs — clearing one
   named boundary, re-running one operation from its checkpoint. Nothing wider.
3. **Treat `recoveryAction` as data, not permission.** Present it; never execute
   it automatically, and never read it as authority to act.
4. **Honor `mutationState`.** Reconcile before retrying `partial-recoverable`;
   never auto-retry `unknown`.
5. **Retry idempotently.** A retry must not duplicate a journal entry, commit,
   archive, merge, branch deletion, checkpoint, or cleanup action.

## What an environment block never authorizes

An environment block is not a lifecycle event. It never authorizes a merge,
branch deletion, archive, force operation, or broad cleanup, and it never
escalates or broadens permissions, runs a recovery action on its own, or
restarts the full lifecycle when a narrower retry exists.

## Compatibility

The fragment is additive. It rides an owning command's existing result object
without changing that object's own schemaVersion, exit code, or fail-closed
semantics. A consumer that does not understand the fragment ignores it and
keeps the command's prior bounded diagnostic; it never accepts partial evidence
in its place.
