# Refresh sd-ai-command-pack to 0.71.38

## Goal

Fleet refresh: install sd-ai-command-pack v0.71.38 (tag v0.71.38 @ 6881aaa3f34fbcc46fddb72ea21476ededc52e58, payload sha256:4046b21a45352cc96aca01dc8578a3be5c2f045c0f878930d0bd2bd9fe8de5e3) into anomaly-metric-creator. Managed scope: installer-managed platform files (claude, gemini, github, opencode), receipts, provenance, and the deterministic repomix map only; no product-code edits. Prepare: bash scripts/update_repomix. Checks: python3 tools/check_ci_review_contract.py, python3 tools/check_copilot_instruction_contract.py. Bound to refresh branch chore/pack-refresh-0.71.38 off base 3b4185ae4a2034197d352dbb81158fdf96d42215. Completion: PR opened, remote review converged, CI green, merged via housekeeping, post-merge audit confirms 0.71.38.

## Requirements

- Install sd-ai-command-pack v0.71.38 (tag `v0.71.38` @ `6881aaa3f34fbcc46fddb72ea21476ededc52e58`, payload `sha256:4046b21a45352cc96aca01dc8578a3be5c2f045c0f878930d0bd2bd9fe8de5e3`) for exactly the claude, gemini, github, and opencode platforms recorded in the fleet manifest.
- Repair the executable bit on `.sd-ai-command-pack/bin/sd-ai-command-pack-review-layout.py`, tracked here as `100644`. This is the payload this release exists to deliver: 0.71.36 corrected the bit in the pack, but the installer returned `unchanged` as soon as a destination's bytes matched, before considering its mode, so no reinstall at any version could have applied it. 0.71.38 fixes that, and the mode change is expected to appear in this diff.
- Limit the diff to installer-managed platform files, `.sd-ai-command-pack/` manifest and provenance receipts, the regenerated `docs/repomix-map.md`, and this task's own `.trellis/` bookkeeping. No product-code edits.
- Run the manifest-ordered preparation and check commands for this consumer (`bash scripts/update_repomix`, then `python3 tools/check_ci_review_contract.py` and `python3 tools/check_copilot_instruction_contract.py`) before the local gate.
- Keep the refresh on branch `chore/pack-refresh-0.71.38` off base `3b4185ae4a2034197d352dbb81158fdf96d42215`, published as a single PR.
- Carry no `trellis update` diff. Trellis version drift is owned separately; a mixed PR stops the lane instead of merging.

## Acceptance Criteria

- [ ] The sd-ai-command-pack install audit passes for all four expected platforms and reports installed payload provenance 0.71.38. It runs from the sd-ai-command-pack source checkout, not from this repository: `python3 scripts/sd-ai-command-pack-install-audit.py --repo <this repository> --expected-platform ...`.
- [ ] `git ls-files -s .sd-ai-command-pack/bin/sd-ai-command-pack-review-layout.py` reports mode `100755`, not `100644`. A refresh that leaves it `100644` has not delivered this release and must not merge.
- [ ] `python3 tools/check_ci_review_contract.py` and `python3 tools/check_copilot_instruction_contract.py` pass after `bash scripts/update_repomix` regenerates `docs/repomix-map.md`.
- [ ] The repository's documented deterministic full local gate passes, or its only findings are dispositioned through the fleet finding severity gate with zero blockers.
- [ ] The refresh is published as one PR whose head carries the work commit plus this task's archive and journal bookkeeping.

> **2026-08-20 — these boxes were left unticked, and stay that way.** This
> archive merged with every acceptance criterion empty because the fleet
> publish path never ticked them and nothing asked the operator to. They are
> not being ticked retroactively: that would assert *this run* verified them,
> while all that can be re-derived today is that the current state satisfies
> them — a different claim, and asserting it after the fact is the same defect
> pointed at its own cleanup. The gap is fixed forward in the pack's
> `fleet-publish-archives-unchecked-criteria` task, which makes the publish
> path tick what it can prove and visibly name what it cannot.

## Post-archive handoff

Owned by the fleet campaign after this task is archived, not by its acceptance
criteria: remote review convergence, CI settle, merge through the consumer
housekeeping gate, refresh-branch deletion, default-branch sync, and the
post-merge audit that confirms the installed pack version is 0.71.38.
