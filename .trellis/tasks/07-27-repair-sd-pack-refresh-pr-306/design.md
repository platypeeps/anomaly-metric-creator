# Design: Repair and review SD command-pack refresh PR 306

## Boundaries and ownership

The repair has two repositories with different sources of truth:

1. `sd-ai-command-pack` owns shipped payload behavior. The canonical classifier
   copy is `templates/scripts/sd-ai-command-pack-review-scope.sh`; its root
   `scripts/` copy is a generated dogfood mirror. `installer/registry.py` owns
   each platform's Trellis-local paths, and `tests/test_install_core.py` checks
   that those paths are represented in shipped scanners.
2. `anomaly-metric-creator` consumes a provenance-vouched installation. It must
   receive the correction through the canonical installer so its manifest,
   payload hashes, docs, and installed scripts remain mutually consistent.

Consumer-local Trellis task, journal, KB, repository-map, and platform-adapter
artifacts remain owned by the consumer repository and its canonical helpers.

## Upstream repair

Create an isolated source-pack worktree from refreshed `origin/main`. Add
`.gemini/settings.json` to the Gemini `trellis_local_only` registry and to the
template shell classifier, then regenerate/synchronize the root mirror. Extend
focused tests so removing the Gemini settings path from either ownership data
or the shell classifier recreates a failure. Treat this as a compatible patch
release, update release metadata, and run the source repository's canonical
release preparation and review lifecycle.

This avoids a consumer-only hash adjustment, which would make provenance claim
a payload the released source never shipped.

## Consumer convergence

Bring current `main` into `automation/sd-ai-command-pack-sync` with a normal
merge commit, preserving public branch history. Run the source installer in
dry-run mode against the consumer and stop on conflicts. After the upstream
release is canonical, apply the refresh through the installer, refresh the
repository map and KB through their helpers, and add only the generated Claude
surfaces that the new commit-by-default policy intentionally exposes.

Before staging, verify the complete untracked inventory and use
`git check-ignore` for representative local-only paths. No generated adapter is
deleted to obtain a clean tree.

## Review and evidence flow

For each changed consumer head:

1. Re-resolve PR head and local head.
2. Run the typed deterministic `sd-check` and disposition every advisory.
3. Push only intended files.
4. Request the configured remote reviewer.
5. Wait for materialized review evidence, then query complete GraphQL thread
   state and CI.
6. Reply to and resolve the original classifier thread only when the released
   payload proves it fixed.

The clean condition is exact-head local validation, provenance validity,
materialized remote review, no unresolved non-outdated threads, green required
CI, and a clean pushed branch.

## Compatibility and rollback

- The classifier addition changes only tooling ownership classification; it
  does not alter AMC runtime behavior.
- The source pack uses a patch release because the fix preserves public command
  and file surfaces.
- Branch updates use merge commits, not rebases or force pushes.
- Upstream work is isolated in a removable worktree; the existing source
  checkout and branch remain untouched.
- If installer dry-run reports conflicts, stop with the exact paths rather than
  forcing replacement.
- If newly trackable Claude files contain machine-specific state, exclude them
  and correct the generating/ignore rule before proceeding.
