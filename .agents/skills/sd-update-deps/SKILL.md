---
name: sd-update-deps
description: Use when open dependency-bot PRs need batch triage — classify every dependabot or renovate PR, merge the safe class sequentially under the housekeeping gate criteria, and park the rest with one-line recommendations.
---

# SD Update Deps

Run this project-local skill for `sd-update-deps` and `/sd:update-deps` style
work. It batch-triages the open dependency-bot pull requests in the current
repository: classify every PR, merge the safe class strictly sequentially
under the housekeeping gate criteria, and park everything else with a
one-line recommendation.

This command triages and merges; it does not upgrade dependencies itself. It
never edits dependency manifests or lockfiles, never pushes to bot branches,
and never opens PRs of its own. The housekeeping gate criteria are the merge
authority: this skill adds no second merge path with weaker criteria.

## When to use

Run this command when dependency-bot PRs have accumulated and the user wants
one batched triage pass instead of PR-by-PR manual decision rounds.

- Scope: open PRs in this repository authored by dependency bots —
  `dependabot` and `renovate`, including their `[bot]` account forms.
- Human-authored PRs are out of scope. Leave them to `sd-review-pr` and
  `sd-housekeeping`.
- Red dependency PRs are parked, not fixed here. Recommend `sd-fix-ci` for
  the ones worth rescuing.
- One repository per run: the current checkout's GitHub repository.
- Positioning: `sd-housekeeping` wraps up one stream's own ready PR; this
  command applies the same merge criteria across the dependency-bot
  backlog. Neither replaces the other.

## Arguments

Arguments arrive as free text with the invocation, as `key=value` pairs and
bare flags. Unknown argument names are an error, not a silent skip: stop and
report them before touching any PR. This skill reads no environment
variables; every tuning knob is an argument.

- `include-runtime-minor` — bare flag. Add runtime-dependency minor updates
  to the auto-merge class for this run. Off by default: without it, runtime
  minors are parked for manual review.
- `dry-run` — bare flag. Classify and emit the full report only; perform no
  merges and no other mutations.

## Workflow

1. Enumerate. List open PRs with `gh pr list` and keep only dependency-bot
   authors. Zero matching PRs is a valid result: report it and stop.
2. Classify every dependency PR on four axes:
   - ecosystem — npm, pip, GitHub Actions, and so on, read from the bot
     metadata and the changed manifest files;
   - semver delta — patch, minor, or major, read from the title and diff;
   - security-advisory linkage — dependency bots flag security updates and
     link the advisory in the PR body or labels;
   - dependency kind — development-only or runtime, read from the manifest
     section the PR changes.
3. Assign each PR exactly one class:
   - Auto-merge class: patch and minor dev-dependency updates, GitHub
     Actions SHA and pin bumps, and security patches.
   - Runtime-dependency minors join the auto-merge class only when
     `include-runtime-minor` was passed.
   - No flag adds a major version update to the auto-merge class —
     majors are always manual.
   - Everything else is parked.
4. With `dry-run`: emit the final report from the classification and stop
   here. No merges, no mutations of any kind.
5. Merge the auto-merge class strictly sequentially, one PR at a time:
   1. Re-verify immediately before each merge: checks green, review
      threads comment-clean, and the PR head current — matching the bot
      branch head and not behind the default branch. Read head, mergeable,
      and merge-state fields with `gh pr view` and check state with
      `gh pr checks`. Dependency bots rebase when the base moves, so
      re-check every remaining auto-class PR after each prior merge;
      classification state from earlier in the run is never merge
      evidence.
   2. Merge only under the housekeeping gate criteria: green,
      comment-clean, mergeable, heads identical. If GitHub refuses the
      merge, park the PR with that refusal as the reason.
   3. After each merge, confirm the default branch stays green before
      starting the next merge. A red default branch stops the merge loop:
      report the remaining auto-class PRs as parked pending a green
      default branch.
   4. Move any PR that fails re-verification to the parked list with the
      failing criterion as its reason, then continue with the next PR.
6. Park everything outside the merged set with one line per PR: the PR
   reference plus a concrete recommendation. Examples:
   - `major — review the changelog and migration notes manually`
   - `runtime minor — rerun with include-runtime-minor after review`
   - `red CI — triage with sd-fix-ci`
   - `unresolved review thread — resolve with the author, then rerun`

## Safety rules

- Sequential merges only. Never merge dependency PRs in parallel, and never
  skip the re-verify and default-branch-green steps between merges.
- Never merge red, commented, or behind PRs.
- Never auto-merge a major version update, with or without flags —
  majors are always manual.
- The housekeeping gate criteria are the merge authority. Never bypass or
  weaken them, and never force a merge that GitHub refuses.
- Never dismiss reviews or resolve other people's review threads to make a
  PR look comment-clean.
- Never edit dependency manifests, lockfiles, or bot branches. This skill
  triages and merges; it does not write code.
- `dry-run` performs no mutations of any kind.

## Final report

The final report is mandatory-shaped: every item below appears in every run,
and an empty item states its emptiness explicitly (write `none`). Keep it
scannable — bullets and short lines, one point per line, no paragraph blobs.

- Classification table: one row per dependency PR —
  PR · ecosystem · delta · class. Class is one of `auto-merge`,
  `manual (major)`, or `parked`.
- Merged list: the PRs merged this run, in merge order, or `none`.
- Parked list: every non-merged PR with its one-line reason or
  recommendation, or `none`.
- Default-branch status: green after the final merge, or the failing checks
  by name.

With `dry-run`, the classification and parked items carry the report;
state Merged as `none` and the default-branch status as currently observed.

Example shape for a mixed run:

```
- Classification:
  - #101 · npm · patch · auto-merge
  - #102 · GitHub Actions · pin bump · auto-merge
  - #103 · pip · minor · parked
  - #104 · npm · major · manual (major)
- Merged: #101, #102 (sequential; default branch green after each)
- Parked:
  - #103 — runtime minor; rerun with include-runtime-minor after review
  - #104 — major; review the changelog and migration notes manually
- Default branch: green
```
