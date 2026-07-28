# Repair and review SD command-pack refresh PR 306

## Goal

Make GitHub PR #306 a provenance-valid, exact-head reviewed SD command-pack
refresh that can pass the repository's deterministic and remote gates without
discarding generated Trellis/Claude surfaces or carrying a known classifier
defect into consumers.

## Background

- PR #306 currently refreshes this repository from command-pack version
  `0.54.0` to `0.55.1` at head
  `376b33e2600dcf06fd510f66f3d09fd276c5b210`.
- GitHub Copilot left one unresolved, non-outdated thread on
  `scripts/sd-ai-command-pack-review-scope.sh:104`: the shell classifier omits
  `.gemini/settings.json` although
  `scripts/sd-ai-command-pack-review-preflight.mjs:3214` classifies it as a
  Trellis-copied path.
- The defect is present in the source-of-truth command-pack template and root
  mirror. The source repository's `installer/registry.py` Gemini
  `trellis_local_only` declaration also omits `.gemini/settings.json`, so its
  existing registry-to-shell coverage cannot detect the mismatch.
- The current command-pack `origin/main` is already version `0.55.4`; a shipped
  compatible correction therefore requires a patch release newer than the
  stale `0.55.1` consumer PR payload.
- PR #306 predates recent `main` journal commits. Its deterministic preflight
  currently reports two append-only journal-history failures until the branch
  is updated from `main`.
- The new `.gitignore` policy intentionally commits shareable `.claude/`
  adapters by default. After the released `0.55.5` refresh, this working copy
  exposes 69 generated agents, Trellis commands, hooks, shared settings, and
  project/Trellis skill files as untracked; local-only settings, caches, logs,
  and temporary files remain ignored.
- The repository's generated Obsidian KB is stale for three copied documents,
  one obsolete generated entry, the dashboard, and the LLM overview.

## Requirements

- R1. Preserve all pre-existing repository work. Do not force-push, rewrite
  branch history, delete generated adapters, or stage local-only Claude state.
- R2. Make the classifier correction in the authoritative
  `sd-ai-command-pack` source repository, keeping `templates/**`, root mirrors,
  the platform registry, release metadata, and focused regression coverage in
  sync. Do not hand-patch the consumer's vouched payload.
- R3. Isolate upstream work from the source repository's existing
  `codex/recover-preserved-task-drafts` checkout by using a dedicated branch and
  worktree based on refreshed `origin/main`.
- R4. Publish and merge the upstream fix through its normal PR, review, CI, and
  release gates before treating the replacement consumer payload as canonical.
- R5. Update the consumer PR branch from current `main` without rewriting
  history, then run a conflict-aware command-pack dry-run before replacing the
  installed payload with the released upstream version.
- R6. Include the shareable `.claude/` agents, Trellis commands, hooks,
  `settings.json`, Trellis skills, and project skills made trackable by the new
  policy. Keep `.claude/settings.local.json` and other documented local state
  ignored and outside the PR.
- R7. Refresh repository-generated KB and repository-map artifacts only through
  their canonical helpers. Preserve the user's prior authorization to delete
  stale generated KB entries; do not overwrite unresolved KB conflicts.
- R8. Address the existing Copilot thread with evidence, resolve it only after
  the generated consumer payload contains the upstream correction, and request
  a fresh configured review for every pushed review-fix head as required by
  `sd-review-pr`.
- R9. Keep unrelated PR #300 and the existing Trellis backlog out of scope.
- R10. Do not modify or open a PR against the upstream Trellis repository. If
  the repair reveals Trellis-owned behavior, report a paste-ready handoff.

## Acceptance Criteria

- [ ] The upstream pack's Gemini platform registry declares
  `.gemini/settings.json` as Trellis-owned, and both shipped shell classifier
  copies classify it consistently with the JavaScript preflight.
- [ ] Focused upstream regression tests fail on the old mismatch and pass after
  the fix; template/root parity and the upstream `make check`/release gates
  pass for the exact published head.
- [ ] The upstream compatible fix has a new patch version, matching changelog
  entry, merged PR, and canonical release identity usable by consumers.
- [ ] Consumer PR #306 includes the released payload with a passing provenance
  audit and no manual divergence from the source pack.
- [ ] Consumer PR #306 contains current `main` history without append-only
  journal failures and includes only the intended shareable `.claude/` files.
- [ ] The canonical KB and repository-map checks report current generated
  artifacts with no unresolved conflicts.
- [ ] Consumer `sd-check` passes with a passing state guard on the exact PR
  head; all applicable first-review advisories have recorded dispositions.
- [ ] The configured remote review materializes for the current head, all valid
  review findings are fixed, rebutted, or explicitly decided, and GraphQL
  reports no unresolved non-outdated review threads.
- [ ] Required GitHub CI is green on the exact consumer head and the branch is
  clean, pushed, and ready for the merge owner.

## Out of Scope

- Unrelated dependency PR #300.
- Existing planned AMC feature and audit tasks.
- Changes to Trellis itself or any upstream Trellis pull request.
