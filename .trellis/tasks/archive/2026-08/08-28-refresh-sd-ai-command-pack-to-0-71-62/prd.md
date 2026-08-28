# Refresh sd-ai-command-pack to 0.71.62

## Goal

Fleet refresh: install sd-ai-command-pack v0.71.62 (tag `v0.71.62` @ `3f5c434c9d8dc2ad78a5bd34d0de7bdba7ac9096`, payload `sha256:b58199c689c580b77d4b520a6de12e5032776d869c5db8d2ce56a87f5347400f`) into anomaly-metric-creator, advancing the thin pin from 0.71.51. Managed scope: installer-managed platform files (claude, gemini, github, opencode), the installer-managed `AGENTS.md` entry-point block, receipts, provenance, and the regenerated repomix map only; no product-code edits. Preparation runs `bash scripts/update_repomix`. Checks: `python3 tools/check_ci_review_contract.py`, `python3 tools/check_copilot_instruction_contract.py`. Bound to refresh branch `chore/pack-refresh-0.71.62` off base `9e2671784f537630145df33c8753be248b2a03e5`. Completion: PR opened, review converged, CI green, merged via housekeeping, post-merge audit confirms 0.71.62.

## Requirements

- Install sd-ai-command-pack v0.71.62 (tag `v0.71.62` @ `3f5c434c9d8dc2ad78a5bd34d0de7bdba7ac9096`, payload `sha256:b58199c689c580b77d4b520a6de12e5032776d869c5db8d2ce56a87f5347400f`) for exactly the claude, gemini, github, and opencode platforms recorded in the fleet manifest. As a converted consumer its platform set is owned by the thin pin, so the printed install command carries no platform flag.
- Carry the eleven releases between the installed 0.71.51 and this target. They are pack-internal tooling corrections with one consumer-visible surface: 0.71.60 added an installer-managed entry-point block to `AGENTS.md` for repositories that already have one, so this refresh writes that block here. The rest do not touch consumer product code — the local review stage gained a `codex` adapter and cheapest-policy fallthrough, its `gito` adapter began transmitting the head as well as the base, remote review stopped conflating a dead provider with a clean one, the pack-version adoption exemption began reading file ownership from the base copy, the finish-work completion receipt learned to walk a proven base-update merge, and review-thread collection stopped conflating a GitHub App with the like-named human account.
- Limit the diff to installer-managed platform files, the `AGENTS.md` managed block, `.sd-ai-command-pack/` manifest and provenance receipts, the regenerated repomix map, and this task's own `.trellis/` bookkeeping. No product-code edits.
- Run the manifest-ordered preparation `bash scripts/update_repomix` before the local gate.
- Keep the refresh on branch `chore/pack-refresh-0.71.62` off base `9e2671784f537630145df33c8753be248b2a03e5`, published as a single PR.
- Carry no `trellis update` diff. Trellis version drift is owned separately; a mixed PR stops the lane instead of merging.

## Acceptance Criteria

- [x] <!-- verify: install-audit release=0.71.62 platforms=claude,gemini,github,opencode --> The sd-ai-command-pack install audit passes for all four expected platforms and reports installed payload provenance 0.71.62. It runs from the sd-ai-command-pack source checkout, not from this repository.
- [x] <!-- verify: lane-evidence id=check-command --> The manifest-ordered check commands `python3 tools/check_ci_review_contract.py` and `python3 tools/check_copilot_instruction_contract.py` both pass.
- [x] <!-- verify: lane-evidence id=deterministic-gate --> The consumer's documented full local gate passes, or its only findings are dispositioned through the fleet finding severity gate with zero blockers.
- [x] <!-- verify: bundle-shape --> The refresh is published as one PR whose head carries the work commit plus this task's archive and journal bookkeeping.

<!-- sd-ai-command-pack:criteria-disposition:start -->
> Every acceptance criterion was verified by the publish run.
<!-- sd-ai-command-pack:criteria-disposition:end -->

## Post-archive handoff

Owned by the fleet campaign after this task is archived, not by its acceptance
criteria: review convergence, CI settle, merge through the consumer
housekeeping gate, refresh-branch deletion, default-branch synchronization, and
the post-merge audit that confirms the installed pack version is 0.71.62.
