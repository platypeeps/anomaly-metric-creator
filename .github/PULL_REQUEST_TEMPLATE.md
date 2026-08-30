## Summary

<!-- 1-3 bullets describing what changed and why. Name every behavior change in the diff. -->
<!-- For broad automation, CI/review, generated/tooling, user-facing docs, or
runtime/server changes, add the applicable explicit scope section described in
docs/DEVELOPMENT_CYCLE.md. Start that section with one of these five canonical
headings:
  Automation scope:
  CI/review scope:
  Tooling/generated scope:
  Docs/user-facing scope:
  Runtime/server scope:
The five headings above are the whole accepted set. Nothing mechanically checks
a PR body against them any more: the guard that did lived in the installed
command pack, which this repository no longer carries, so this list and
docs/DEVELOPMENT_CYCLE.md are the authority and a missing section is caught in
review, not by a gate. -->

## Test plan

<!-- Bulleted markdown checklist of TODOs for testing this PR. Include focused
checks first, then the local gate or remote full CI when relevant. -->

- [ ] Focused local checks:
- [ ] Local deterministic gate: `.venv/bin/pre-commit run --all-files && node scripts/check-review-preflight.mjs`
- [ ] Full local gate or remote `full-ci` label needed? _yes/no, with reason_

## Pre-PR checklist

<!--
Mirrors the 15 review headings in docs/spec/amc/backend/testing-quality.md and
docs/spec/amc/backend/documentation-review.md, which carry the per-heading
bullets. For each item below: tick the box once you have confirmed it, or replace
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
