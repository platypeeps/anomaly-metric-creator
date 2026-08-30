---
title: Refresh sd-ai-command-pack to 0.71.6
status: done
created: 2026-08-14
---
# Refresh sd-ai-command-pack to 0.71.6

Fleet campaign `refresh-0.71.6-20260814T170234Z`, consumer `anomaly-metric-creator`.

## Goal

Move the installed pack from 0.71.5 to 0.71.6. The release ships the
`generated structural map paths` review-preflight check (fails a committed
map naming a missing `.trellis/` path) and the reordered `pr-publication`
publication sequence (publish helper before push).

## Ownership

The refresh changes only installer-managed payload, receipts/provenance,
this task's artifacts, and deterministic preparation output. Consumer product
code is untouched.

## Validation

- Installer reports the vouched-upgrade path (no conflicts, no --force).
- Install audit passes; provenance reads 0.71.6.
- Candidate preparation and checks pass from this checkout.
- The consumer's full deterministic check gate passes.

## Completion criteria

- [ ] Pack 0.71.6 installed with clean audit and provenance.
- [ ] Candidate preparation output regenerated and committed.
- [ ] Full check gate green on the refresh head.
- [ ] PR merged through the housekeeping gate; post-merge provenance reads
      0.71.6. (Merge and post-merge verification are the post-archive
      handoff, owned by the fleet campaign.)
