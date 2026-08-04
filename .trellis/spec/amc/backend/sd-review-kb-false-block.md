# Runbook: `knowledge.obsidian-kb` review/merge false-block

Durable operational note for the `sd-review` / `sd-housekeeping` merge gate.
Source of the mechanism: `.trellis/tasks/08-03-sd-review-kb-coordinator-false-positive/research/root-cause.md`
(confirmed 2026-08-04). Fix design:
`.trellis/tasks/08-03-sd-review-kb-coordinator-false-positive/design.md`.

## Symptom

`sd-review scope=pr` (and the `sd-housekeeping` merge gate) return **blocked**
on the deterministic `sd-check` row `knowledge.obsidian-kb`, reporting a stale
copy count, while a standalone
`python3 scripts/sd-ai-command-pack-update-spec-kb.py --check` on the same head
passes clean (exit 0). The block reproduces only under the coordinator, never
under a fresh standalone `--check`. Observed on PR #316 and PR #324.

## Mechanism (confirmed)

`.obsidian-kb` in this repo is an **absolute symlink to a live external Obsidian
vault** (`/.gitignore:19:/.obsidian-kb`; untracked) that mutates continuously
and independently of repo HEAD. The coordinator's KB row runs
`update-spec-kb.py --check` against that live working tree (there is **no**
`/tmp` snapshot — the prd's original snapshot-undercount hypothesis is refuted),
so the check fails non-deterministically whenever the vault is momentarily
inconsistent (`present != expected` mid-edit). The coordinator memoizes each
`sd-check` verdict per `(headOid, configurationDigest)` with `worktreeDigest`
that **excludes gitignored paths** (`review.py:1706`), so a transient failure is
frozen into the cache key and never clears against a live re-check.

## Posture decision (AC2)

A deterministic review/merge gate **must not block on an artifact that is
gitignored, external, live-mutating, and never shipped.** The KB freshness check
is **advisory (non-blocking)** for an external-symlinked `.obsidian-kb`: it may
report drift as information but must not contribute a blocking verdict to
`sd-check`, and must never gate a GitHub merge whose authoritative `CI Result`
gate is green. A repo that legitimately *commits* its `.obsidian-kb` (a real
directory, or an in-repo symlink) keeps the deterministic gate — the downgrade
is scoped to the external-symlink case only.

## Durable fix (upstream)

The `scripts/*.py` are vendored from `platypeeps/sd-ai-command-pack`
(synced by `.github/workflows/sd-ai-command-pack-sync.yml`), so the code fix is
an upstream change in `kb_freshness_row` (advisory downgrade guarded by
`_is_external_symlink`). This repo receives it later via the sync automation PR;
do not hand-edit the vendored `scripts/` copy here. Track under task
`08-03-sd-review-kb-coordinator-false-positive`.

## Local workaround until the fix syncs (AC3)

Until the upstream advisory fix lands and syncs in, merge a PR whose only
blocker is this KB row via the **authoritative green GitHub gate**, with
explicit operator greenlight:

1. Confirm the GitHub gate is genuinely green — `CI Result` passing,
   conversation threads resolved, `mergeStateStatus: CLEAN`.
2. Verify the coordinator's KB row is the *only* blocker and that a standalone
   `update-spec-kb.py --check` passes clean (proving the block is the transient
   false-negative, not real drift).
3. Merge through the green GitHub gate. This bypasses **only** the local KB
   false-negative — never GitHub branch protection, CI, or conversation
   resolution (AC4 preserved).

Used for PR #316 and PR #324. It is an operator judgment call each time, not a
standing auto-merge: the autonomous work loop surfaces it for greenlight rather
than merging through the false-block itself.
