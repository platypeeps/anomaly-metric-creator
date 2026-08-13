# SD AI command pack refresh to 0.71.2

## Context

This repository vendors the SD AI command pack. The installed payload is at
0.71.1; the fleet target release is 0.71.2. The refresh is driven by the
sd-fleet-refresh campaign from the pack source checkout, which owns the install
command, the audit, and the merge gate. This task exists so the refresh has a
Trellis home in this repository for its planning artifacts, journal session, and
archive record.

## Requirements

1. Install pack release 0.71.2 into this checkout for the platforms this
   consumer is configured for: claude, gemini, github, and opencode. Use only
   the install command emitted by fleet preflight; the installer is
   authoritative for which files change.
2. Verify the installed payload with the pack's install audit. The audit must
   report the full manifest target count for all four platforms, provenance
   version 0.71.2, and matching vouched file hashes.
3. Confine the change to installer-managed and generated output plus this task's
   own bookkeeping. No product code, test, or configuration changes belong in
   this refresh. If the refresh surfaces a pre-existing consumer-authored defect
   that blocks a gate, repair it in the smallest correct form and name it
   separately in the pull request body.
4. Pass this repository's documented local gate before requesting review, and
   pass CI and remote review before the campaign merges through the housekeeping
   gate.

## Constraints

- The working tree must be clean before the install and must not accumulate
  changes outside the installer-managed allowlist.
- The pull request must distinguish copied/generated pack scope from any
  consumer-authored scope so the remote reviewer does not review vendored pack
  content line-by-line.

## Acceptance Criteria

- [ ] Pack 0.71.2 is installed for claude, gemini, github, and opencode.
- [ ] The install audit passes with provenance version 0.71.2 and matching
      vouched hashes.
- [ ] The diff against the base commit contains only installer-managed output,
      generated output, and this task's bookkeeping, or names any other path
      explicitly in the pull request body.
- [ ] The documented local gate passes on the candidate head.
- [ ] The pull request body separates copied/generated scope from
      consumer-authored scope.

## Post-archive handoff

Owned by the fleet campaign after this task is archived, not by its acceptance
criteria: remote review convergence, CI settle, merge through the consumer
housekeeping gate, refresh-branch deletion, default-branch sync, and the
post-merge audit that confirms the installed pack version is 0.71.2.
