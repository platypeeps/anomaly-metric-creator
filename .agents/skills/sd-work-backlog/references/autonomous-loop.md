# Autonomous Loop Planning Reference

Use this reference only after `sd-work-backlog` has selected one task and
persisted the selection. It defines planning quality for both `selector=all`
and the typed `selector=needs-design` path.

## Converge The PRD

Confirm `prd.md` has a concrete goal, boundaries, requirements, acceptance
criteria, and known decisions. Preserve existing useful content. Resolve
repository-answerable questions from specs and source; mark genuine product,
security, credential, or external-state decisions as blockers rather than
inventing an answer.

If the task cannot form one coherent, reviewable pull request, split it into
ordered Trellis tasks before implementation. Record parent/child ordering and
leave each task independently actionable.

## Create Or Update `design.md`

Inspect only the smallest source, docs, and spec set needed to ground the
proposal. Prefer repository-native search and existing patterns.

For a new artifact, use:

```markdown
# <Task Title> Design

## Overview

## Proposal

## Boundaries And Non-Goals

## Affected Files

## Data And Command Contracts

## Risks And Edge Cases

## Validation
```

When useful content exists, preserve it and fill placeholders or append a
dated, clearly labeled proposal. Include ownership boundaries, state/data
shape, command/config surfaces, failure behavior, idempotency, portability,
security/privacy concerns, rollout, and rollback where applicable.

## Create Or Update `implement.md`

Convert the design into an ordered execution path another session can follow
without rediscovering architecture. Include:

- small first step and dependency order;
- canonical source files and generated/mirrored outputs;
- focused and broad validation commands;
- docs/spec/release metadata updates;
- reviewer-sensitive risks and boundary cases;
- rollback points; and
- follow-ups that are explicitly outside the current PR.

For a new artifact, use:

```markdown
# <Task Title> Implementation Plan

## Execution Order

## Validation Plan

## Documentation And Spec Updates

## Review Notes

## Rollback Points

## Follow-Ups
```

## Planning Exit Check

Before transitioning to implementation or stopping at `until=design`, verify:

1. PRD, design, and implementation plan agree on scope and terminology.
2. Every acceptance criterion maps to an implementation or validation step.
3. Affected ownership surfaces and generated copies are named.
4. Risks have deterministic prevention or explicit review guidance.
5. No unresolved question requires guessing.
6. The task fits one coherent pull request or has been split.

If the exit check fails because user input is unavoidable, use the controller's
single-question wait and parking protocol. Do not write a fake implementation
path merely to make the artifact appear complete.
