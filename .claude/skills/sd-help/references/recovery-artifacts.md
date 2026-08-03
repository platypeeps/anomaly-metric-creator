# Recovery-artifact ownership lifecycle

> The single ownership boundary for pack-created Git recovery artifacts — the
> stashes and worktrees a workflow makes to protect uncommitted work before a
> risky operation. It separates the workflow that creates and owns an artifact
> from the read-only reporting and the proof-gated general cleanup that follow.
> Enforced by `sd-ai-command-pack-recovery-artifacts.py`; surfaced by
> `sd-status`; reconciled by `sd-housekeeping`.

## Why a receipt exists

A pack recovery artifact is a Git object that holds work no commit yet holds: a
stash of a dirty tree, or a scratch worktree. Deleting one blindly can destroy
the only copy of that work, so no artifact is ever cleaned on inference.

Every pack-created artifact has a **receipt** — a versioned, user-local JSON
record keyed by repository identity and a unique artifact ID. The receipt is the
sole proof of ownership and intent: what the artifact is, which run created it,
its original head, and the predicate under which it becomes safe to retire.
Cleanup acts only through receipts; an artifact with no receipt is *unowned* and
is never touched. The receipt is private (owner-only permissions), bounded, and
carries no secrets, remote URLs, or raw filesystem errors.

## The three roles

Ownership moves through three roles, and only two of them ever delete:

1. **The creating workflow** owns the artifact from creation through the
   success path. It registers the receipt atomically, the instant after the
   artifact exists, and it removes its own artifact and receipt in a `finally`
   once recovery succeeds. It is the only *owner-mode* cleanup caller.
2. **`sd-status`** reports every artifact read-only and classifies it. It never
   creates, repairs, or deletes a receipt or Git artifact.
3. **`sd-housekeeping`** is the sole general cleanup owner. It retires only
   artifacts it can *prove* are safe and preserves everything else for a status
   decision. It never prunes receipts.

No other workflow deletes a pack recovery artifact, and none runs broad
`git clean`, destructive reset, or unverified stash/worktree deletion.

## The lifecycle a creating workflow follows

A workflow that creates a pack stash or worktree owns its whole success path:

1. **Create**, then **register immediately.** Registration is atomic and
   caller-owns-rollback: if `register` fails, the workflow removes the just-made
   artifact through owner-mode cleanup, or stops with a diagnostic. It never
   proceeds leaving an unregistered artifact behind.

   ```bash
   artifact_id="$(bash scripts/sd-ai-command-pack-toolchain.sh run-python -- \
     scripts/sd-ai-command-pack-recovery-artifacts.py register \
       --repo . --type stash --object "$oid" --subject "$subject" \
       --created-by "$skill" --run-id "$run_id" --purpose "$why" \
       --original-head "$head" --json | jq -r .artifactId)"
   ```

2. **Do the risky work.**

3. **On the success path, retire in a `finally`.** After recovery succeeds, the
   creating workflow removes its own artifact and receipt through owner mode,
   naming the exact artifact:

   ```bash
   bash scripts/sd-ai-command-pack-toolchain.sh run-python -- \
     scripts/sd-ai-command-pack-recovery-artifacts.py cleanup \
       --repo . --mode owner --artifact-id "$artifact_id" --json
   ```

   **Interruption preserves both.** If the workflow dies before the `finally`,
   the artifact and its receipt survive by design — that is the recovery path,
   and `sd-status` will surface it for a human decision.

Owner mode is targeted: it acts on exactly one `--artifact-id`, and it alone may
prune a receipt whose artifact is already gone (the success-path record).

## How status classifies (read-only)

`sd-status` embeds a bounded recovery summary. Every artifact carries exactly
one classification:

- `active` — the owning run is still live; leave it alone.
- `safe-cleanable` — the retire predicate is proven; housekeeping may retire it.
- `needs-review` — a receipt exists but safety is unproven; a human decides.
- `missing-artifact` — the receipt's Git object is gone; nothing to delete.
- `unowned-artifact` — a Git artifact with no pack receipt; never pack-touched.

Status only reports. It moves nothing, whatever the classification.

## How housekeeping reconciles (proof-gated)

`sd-housekeeping` runs the general sweep in `--mode housekeeping` after its
branch and merge work and before its status report. It retires an artifact only
against an exact, current proof and defaults to preserve on any ambiguity:

- A **worktree** may be removed only at its exact registered path, clean, with a
  matching common Git directory, no lock, and its head reachable or explicitly
  retained.
- A **stash** may be dropped only at its exact object id, proven redundant or
  superseded so no unique work is lost.

Anything unproven — `needs-review`, ambiguous, or holding unique content —
stays. Housekeeping surfaces each retired artifact as an action and each refused
or failed retire as an anomaly; it never prunes receipts and never forces a
removal. A missing or foreign artifact is reconciled conservatively: reported,
never deleted.

## Scope

This contract applies prospectively to any workflow that creates a pack recovery
stash or worktree. It adds no new artifact creation to existing skills; it
defines the register-then-`finally` protocol they must follow when they do, and
the read-only and proof-gated boundaries that `sd-status` and `sd-housekeeping`
already enforce.
