# Refresh sd-ai-command-pack to 0.71.63

## Goal

Fleet refresh: install sd-ai-command-pack v0.71.63 (tag v0.71.63 @ 0f6bba5733689071471401016181e90e27073395, payload sha256:dc80bb40bd1dc69cf5a99d4e401238d12fae6b5ad1bfe499181cc389e7797131) into anomaly-metric-creator, advancing the thin pin from 0.71.62. Managed scope: installer-managed platform files (claude, gemini, github, opencode), receipts, provenance, and the deterministic repomix map only; no product-code edits. Check: python3 tools/check_ci_review_contract.py && python3 tools/check_copilot_instruction_contract.py. Bound to refresh branch chore/pack-refresh-0.71.63 off base 4538fa9b53c1b0ab8725cfbe9d95b9e8c4881f8f. Completion: PR opened, remote review, CI green, merged via housekeeping, post-merge audit confirms 0.71.63.

## Requirements

- Install sd-ai-command-pack v0.71.63 (tag `v0.71.63` @ `0f6bba5733689071471401016181e90e27073395`, payload `sha256:dc80bb40bd1dc69cf5a99d4e401238d12fae6b5ad1bfe499181cc389e7797131`) for exactly the claude, gemini, github, and opencode platforms recorded in the fleet manifest. As a converted thin consumer its platform set is owned by the pin, so the printed install command carries no platform flag.
- Carry the single release between the installed 0.71.62 and this target. It is a pack-internal review-stage correction with no consumer product-code surface: the local review stage's bookkeeping-evidence remedy now names the stage script it tells the operator to run. The regenerated `sd-help` command catalog follows from that same change.
- Limit the diff to installer-managed platform files, `.sd-ai-command-pack/` manifest and provenance receipts, deterministic generated output, and this task's own `.trellis/` bookkeeping. No product-code edits.
- Run the manifest-ordered preparation and check commands for this consumer before the local gate.
- Keep the refresh on branch `chore/pack-refresh-0.71.63` off base `4538fa9b53c1b0ab8725cfbe9d95b9e8c4881f8f`, published as a single PR.
- Carry no `trellis update` diff. Trellis version drift is owned separately; a mixed PR stops the lane instead of merging.

## Acceptance Criteria

- [ ] <!-- verify: install-audit release=0.71.63 platforms=claude,gemini,github,opencode --> The sd-ai-command-pack install audit passes for all four expected platforms and reports installed payload provenance 0.71.63. It runs from the sd-ai-command-pack source checkout, not from this repository.
- [ ] <!-- verify: lane-evidence id=check-command --> `python3 tools/check_ci_review_contract.py && python3 tools/check_copilot_instruction_contract.py` passes.
- [ ] <!-- verify: lane-evidence id=deterministic-gate --> The documented local gate passes, or its only findings are dispositioned through the fleet finding severity gate with zero blockers.
- [ ] <!-- verify: bundle-shape --> The refresh is published as one PR whose head carries the work commit plus this task's archive and journal bookkeeping.

## Post-archive handoff

Owned by the fleet campaign after this task is archived, not by its acceptance
criteria: remote review convergence, CI settle, merge through the consumer
housekeeping gate, refresh-branch deletion, default-branch sync, and the
post-merge audit that confirms the installed pack version is 0.71.63.
