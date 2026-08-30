---
title: Refresh sd-ai-command-pack to 0.71.33
status: done
created: 2026-08-19
branch: chore/pack-refresh-0.71.33
---
# Refresh sd-ai-command-pack to 0.71.33

## Goal

Fleet refresh: install sd-ai-command-pack v0.71.33 (tag v0.71.33 @ 6c6d05a6450e1d52b22b0b08d8f275d4af358115, payload sha256:0fe1997c752034d6ce6231c235565ac7c79e8c369a42561f24ad1e9dbc67667a) into anomaly-metric-creator, replacing the 0.71.22 pin. Managed scope: installer-managed platform files (claude, gemini, github, opencode), receipts, provenance, and the deterministic repomix map only; no product-code edits. Prepare: bash scripts/update_repomix. Check: python3 tools/check_ci_review_contract.py, then python3 tools/check_copilot_instruction_contract.py. Bound to refresh branch chore/pack-refresh-0.71.33 off base 334a49ecb6bcf7b0bb894e694d5df0a104e4dd06. Completion: PR opened, remote review, CI green, merged via housekeeping, post-merge audit confirms 0.71.33.

## Requirements

- Install sd-ai-command-pack v0.71.33 (tag `v0.71.33` @ `6c6d05a6450e1d52b22b0b08d8f275d4af358115`, payload `sha256:0fe1997c752034d6ce6231c235565ac7c79e8c369a42561f24ad1e9dbc67667a`) for exactly the claude, gemini, github, and opencode platforms recorded in the fleet manifest. This consumer is a thin install: its platform set is owned by its pin, so the refresh carries no `--platform` flag.
- Limit the diff to installer-managed platform files, `.sd-ai-command-pack/` manifest and provenance receipts, the regenerated structural map, and this task's own `.trellis/` bookkeeping. No product-code edits.
- Run this consumer's manifest-ordered commands in order: `bash scripts/update_repomix` to prepare, then `python3 tools/check_ci_review_contract.py` and `python3 tools/check_copilot_instruction_contract.py`.
- Run the repository's declared full local gate before publishing.
- Keep the refresh on branch `chore/pack-refresh-0.71.33` off base `334a49ecb6bcf7b0bb894e694d5df0a104e4dd06`, published as a single PR.
- Carry no `trellis update` diff. Trellis version drift is owned separately; a mixed PR stops the lane instead of merging.
- Leave the 20 pre-existing `planning` tasks in this repository untouched. This refresh owns only its own task directory.

## Acceptance Criteria

- [x] The pack install audit, run from the sd-ai-command-pack source checkout
  with `--repo` pointed at this repository, passes for all four expected
  platforms and reports installed payload provenance 0.71.33.
- [x] This consumer's manifest-ordered check commands pass after the structural map is regenerated.
- [x] The declared full local gate passes, or its only findings are dispositioned through the fleet finding severity gate with zero blockers.
- [x] The refresh is committed as exactly one work commit on `chore/pack-refresh-0.71.33`, containing only installer-managed paths, the regenerated map, and this task's own directory.
- [x] The 20 pre-existing `planning` tasks are unchanged by this refresh.

## Notes

`docs/repomix-map.md` regenerated with no diff on this refresh, so this
consumer does not carry the stale-map defect seen elsewhere in the fleet.

The 0.71.33 review guard fails this repository's local gate with two
missing-path findings in `docs/DEVELOPMENT_CYCLE.md`, at lines 236 and 251.
Both sit inside a section titled "Local review-gate helper forwarders
(retired)" whose whole point is to name the files that were deleted and say
they must not come back. The file is byte-identical to `origin/main` --
`git diff --stat origin/main -- docs/DEVELOPMENT_CYCLE.md` is empty -- and this
refresh touches no documentation path. The findings are pre-existing content
debt that the newer guard detects, not a regression from the refresh. They were
dispositioned through the fleet finding severity gate as contract family
`consumer-unrelated`, final disposition `defer-follow-up`, with the gate
returning `continue-with-follow-ups` and zero blockers. Source follow-up:
`08-18-preflight-path-refs-ignore-aware` in `sd-ai-command-pack`, which owns
making the guard's missing-path rule aware of prose that deliberately cites
removed files.

## Post-archive handoff

Owned by the fleet campaign after this task is archived, not by its acceptance
criteria: publish the branch as one PR whose head carries the work commit plus
this task's archive and journal bookkeeping, merge through the housekeeping
gate, delete the refresh branch, synchronize the default branch, and record the
post-merge install audit as the lane's `post-merge-verification` receipt. This
is the campaign's final cohort, so its merge closes the 0.71.33 rollout.
