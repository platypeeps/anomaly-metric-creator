## Summary

<!-- 1-3 bullets describing what changed and why. Name every behavior change in the diff. -->
<!-- For broad automation, CI/review, generated/tooling, user-facing docs, or
runtime/server changes, add the applicable explicit scope section described in
docs/DEVELOPMENT_CYCLE.md. -->

## Test plan

<!-- Bulleted markdown checklist of TODOs for testing this PR. Include focused
checks first, then the local gate or remote full CI when relevant. -->

- [ ] Focused local checks:
- [ ] Local deterministic gate: `SD_AI_COMMAND_PACK_FULL_CHECK_PRISM=0 SD_AI_COMMAND_PACK_FULL_CHECK_GITO=0 bash scripts/sd-ai-command-pack-full-check.sh`
- [ ] Full local gate or remote `full-ci` label needed? _yes/no, with reason_

## Pre-PR checklist

<!--
Mirrors the 14 review headings in .trellis/spec/amc/backend/testing-quality.md and
.trellis/spec/amc/backend/documentation-review.md. CLAUDE.md remains expanded source
detail. For each item below: tick the box once you have confirmed it, or replace
the box line with "N/A — _reason_".
-->

- [ ] Scope & description
- [ ] Validators and schema checks
- [ ] Doc / docstring sync
- [ ] Single source of truth
- [ ] Completeness
- [ ] Mode / flag combinations
- [ ] Test path determinism
- [ ] Performance in hot paths
- [ ] Action order in user-facing output
- [ ] Test hygiene
- [ ] Test resource cost
- [ ] Cross-platform test guards
- [ ] Default-behavior changes
- [ ] CI / workflow / dependency hygiene
- [ ] Changelog / version impact
