# Refresh sd-ai-command-pack to 0.71.45

## Goal

Fleet refresh: install sd-ai-command-pack v0.71.45 (tag v0.71.45 @ 0db7a890099b3c410b3a70b0355314855105e287, payload sha256:19fa66d697d4388b9c616b3780dfc58e2c4be4ce6ab7000d66124e76ca812ec5) into anomaly-metric-creator, advancing the thin pin from 0.71.38. Managed scope: installer-managed platform files, `.sd-ai-command-pack/` manifest and provenance receipts, the regenerated `docs/repomix-map.md`, and this task's own `.trellis/` bookkeeping; no product-code edits. Prepare: bash scripts/update_repomix. Check: `python3 tools/check_ci_review_contract.py` and `python3 tools/check_copilot_instruction_contract.py`. Bound to refresh branch chore/pack-refresh-0.71.45 off base main. Completion: PR opened, remote review, CI green, merged via housekeeping, post-merge audit confirms 0.71.45.

## Requirements

- Install sd-ai-command-pack v0.71.45 (tag `v0.71.45` @ `0db7a890099b3c410b3a70b0355314855105e287`, payload `sha256:19fa66d697d4388b9c616b3780dfc58e2c4be4ce6ab7000d66124e76ca812ec5`) for exactly the claude, gemini, github, and opencode platforms recorded in the fleet manifest. As a converted consumer its platform set is owned by the thin pin, so the printed install command carries no platform flag.
- Carry the releases between the installed 0.71.38 and this target. They are pack-internal tooling corrections with no consumer product-code impact: the review preflight gained the checks it never ran for citations under the `.claude/` and `.gemini/` prefixes; an `optionalReferencePaths` exemption stopped being lost whenever the path was cited as a location; the `sd-review`, `sd-housekeeping`, and `sd-review-learnings` adapters had prose corrected that named helper paths a thin install does not resolve; `sd-audit-repo` stopped describing its charter search root as repository-relative, which a thin install has no copy of; the fleet publish helper stopped archiving a task with every acceptance criterion left unverified; `make audit` stopped reporting success with no scanner installed.
- Limit the diff to installer-managed platform files, `.sd-ai-command-pack/` manifest and provenance receipts, the regenerated `docs/repomix-map.md`, and this task's own `.trellis/` bookkeeping. No product-code edits.
- Run the manifest-ordered preparation and check commands for this consumer (`bash scripts/update_repomix`, then `python3 tools/check_ci_review_contract.py` and `python3 tools/check_copilot_instruction_contract.py`) before the local gate.
- Keep the refresh on branch `chore/pack-refresh-0.71.45` off base `main`, published as a single PR.
- Carry no `trellis update` diff. Trellis version drift is owned separately; a mixed PR stops the lane instead of merging.

## Acceptance Criteria

- [ ] <!-- verify: install-audit release=0.71.45 platforms=claude,gemini,github,opencode --> The sd-ai-command-pack install audit passes for all four expected platforms and reports installed payload provenance 0.71.45. It runs from the sd-ai-command-pack source checkout, not from this repository.
- [ ] <!-- verify: lane-evidence id=check-command --> `python3 tools/check_ci_review_contract.py` and `python3 tools/check_copilot_instruction_contract.py` both pass after `bash scripts/update_repomix` regenerates `docs/repomix-map.md`.
- [ ] <!-- verify: lane-evidence id=deterministic-gate --> `SD_AI_COMMAND_PACK_FULL_CHECK_PRISM=0 SD_AI_COMMAND_PACK_FULL_CHECK_GITO=0 bash ~/.agents/bin/sd-ai-command-pack-full-check.sh` passes, or its only findings are dispositioned through the fleet finding severity gate with zero blockers.
- [ ] <!-- verify: bundle-shape --> The refresh is published as one PR whose head carries the work commit plus this task's archive and journal bookkeeping.

## Post-archive handoff

Owned by the fleet campaign after this task is archived, not by its acceptance
criteria: remote review convergence, CI settle, merge through the consumer
housekeeping gate, refresh-branch deletion, default-branch sync, and the
post-merge audit that confirms the installed pack version is 0.71.45.
