# Completion versus housekeeping lifecycle

> The single ownership boundary shared by `sd-finish-work`, `sd-review-pr`,
> `sd-ship`, and `sd-housekeeping`. It separates the work a task must complete
> before Trellis archives it from the merge and cleanup follow-through that can
> only happen afterward.

## The boundary

An acceptance criterion is any outcome that must be true before Trellis archives
the task. Every such criterion is satisfied — and its checkbox checked — before
`task.py archive` marks the task `completed`, so an archived task record is
truthful at the moment of archive.

Merge, branch deletion, default-branch synchronization, superseded-PR closure,
and post-merge fleet checks cannot occur before archive. They are the
**post-archive handoff**, never left as unchecked acceptance criteria. A task
that presents post-archive mutations as unchecked acceptance criteria is stale
guidance: it makes a `completed` record contradict its own checklist, exactly
the contradiction Copilot flagged on `platypeeps/rwbp-coordinator` PR #187
(`discussion_r3661730449`).

## The one named place

Downstream obligations that can only occur after archive belong under one
task-document heading:

```markdown
## Post-archive handoff

- Merge the reviewed exact head through `sd-housekeeping` (sole merge owner).
- Delete the merged feature branch and synchronize the default branch.
- Close any pull request this work supersedes.
- <post-merge fleet or consumer follow-through, when the task has any>
```

`## Post-archive handoff` is the consistently named representation for these
obligations across every task document. It is prose handoff, not a criteria
checklist: its bullets are never `- [ ]` acceptance-criteria checkboxes, so they
can never be mistaken for incomplete task acceptance criteria.

## Ownership sequence

The boundary preserves the existing sequence and gates; it moves no authority:

1. `sd-finish-work` — validate the implementation and task record, capture the
   finalization base, run the pre-archive gate, then archive, journal, and emit
   the typed finalization receipt. It is the pre-archive completion owner and
   never merges.
2. `sd-review-pr` — settle exact-head review, unresolved threads, and CI on the
   published head. Review readiness is evidence, not a merge.
3. `sd-housekeeping` — the sole merge and cleanup mutation owner. It performs the
   post-archive handoff behind its exact-head eligibility, merge, and deletion
   gates.

`sd-ship` sequences these stages and owns none of their gates; merge authority
stays in `sd-housekeeping` alone.

## Authoring examples

### Normal implementation task

Acceptance criteria describe only the pre-archive result:

```markdown
## Acceptance Criteria

- [ ] The parser rejects malformed input with a typed error and exit code 2.
- [ ] Focused tests cover the valid, empty, and malformed cases.
- [ ] Templates, root copies, and `make check` are green.

## Post-archive handoff

- Merge the reviewed head through `sd-housekeeping`, then delete the branch and
  synchronize the default branch.
```

The task archives truthful the moment every criterion is checked; the merge is
handoff, not a criterion.

### Planning-only finalization

A planning task finalizes with no merge of its own. Its acceptance criteria are
the planning artifacts; it still records the downstream handoff:

```markdown
## Acceptance Criteria

- [ ] `prd.md`, `design.md`, and `implement.md` capture the approved contract.
- [ ] The finding-to-task ownership map is complete with no unowned gap.

## Post-archive handoff

- The umbrella branch carries this planning surface as its first commit; no
  planning-only PR is opened. Implementation continues on that branch.
```

Planning finalization skips the archive-only pre-archive gate by design; it does
not weaken it.

### Task with post-archive fleet or cleanup follow-through

Fleet rollout and consumer cleanup are post-archive by nature:

```markdown
## Acceptance Criteria

- [ ] The stabilized release candidate builds, and its ledger evidence is
      current.

## Post-archive handoff

- Merge through `sd-housekeeping`, publish the successor release, then run the
  bounded fleet refresh once against the named consumers.
- Close the superseded release-candidate PRs after the successor merges.
```

None of these bullets is an acceptance criterion, and none introduces a second
merge authority: `sd-housekeeping` still owns every merge.

## Scope

This contract applies prospectively. Historical archived tasks are not rewritten
to match it; known contradictions may be reconciled in their owning
repositories.
