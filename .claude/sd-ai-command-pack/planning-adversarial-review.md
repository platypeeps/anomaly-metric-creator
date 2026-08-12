# Planning adversarial review contract

Use this contract at the planning convergence boundary when the current run
creates or materially updates an active Trellis task's `prd.md`, `design.md`,
or `implement.md`. It supplements the active Trellis planning workflow without
modifying or replacing Trellis.

## 1. Trigger and baseline

Before the first planning-artifact write in a coherent edit batch, record for
each of `prd.md`, `design.md`, and `implement.md` whether it exists and, when it
does, its content hash. Compare those baselines at the convergence boundary.

Run the review when at least one artifact is new or materially changed. Skip it
with a visible reason when there is no active task, none of the three files
changed, or the differences are only whitespace, formatting, generated
metadata, or equivalent non-semantic churn. Do not invoke a review after every
individual file write; review the coherent artifact set once.

## 2. Review lanes

The host must perform its own adversarial review of the changed artifact set.
Challenge requirements, assumptions, design fit, failure modes, security,
operability, validation, rollout, rollback, and whether `implement.md` closes
the commitments in `prd.md` and `design.md`. Verify claims against repository
code, specs, and task context rather than accepting any review lane at face
value, including your own.

Also check the task's artifacts against each other, not only against the
repository. A measurement, count, size, path, or identifier usually appears in
more than one of `prd.md`, `design.md`, `implement.md`, and `task.json`, and one
artifact often cites what another "states". Correcting a figure in one place
leaves the others asserting the old value, and correcting the cited artifact can
invalidate the citation itself. Enumerate every occurrence of each such value
across the task directory, confirm they agree, and confirm any cross-artifact
citation still describes what its target actually says. Search for each value
instead of reading the artifacts in sequence: the stale copy is the one you did
not think to open. Widen the search past the task directory only when the value
is also cited outside it, such as in a spec, report, or ledger.

This contract defines exactly one lane: the host's own. A repository may define
an additional independent lane of its own, outside this contract, and sections
3 through 5 keep a place for one; the pack ships none. Assume you are the whole
review -- hold it to the standard that two lanes would have met, because nothing
else will catch what it misses.

## 3. Concern disposition

Merge and deduplicate every lane that ran into one concern ledger. Assign
stable IDs `C-1`, `C-2`, and so on. For every material concern record:

- severity and whether it blocks implementation;
- the repository or artifact evidence used to verify it;
- one disposition: `addressed`, `rebutted`, `parked`, or `unresolved`;
- the owning artifact and change when addressed, or the evidence when
  rebutted;
- the trigger and owner when parked.

Update the owning planning artifact for every supported concern. Rebut an
unsupported concern with evidence instead of changing the plan to satisfy it.
Park external, product, or deliberately deferred work explicitly; a parked
blocking concern still blocks implementation. An unresolved blocker prevents
implementation approval and prevents `task.py start`.

## 4. Convergence limit

When addressed concerns change a planning artifact, rerun the host review
against the updated artifact set, plus a fresh run of any additional lane that
was available in the initial round. Reconcile each remediation round through
the same ledger. Run at most two remediation rounds
(three automatic rounds total); do not start a fourth automatic round.

Expect a remediation round to find defects the previous round's own fixes
introduced. A value corrected in one artifact and left standing in another is
the common shape, which is why the cross-artifact sweep above belongs to every
round rather than only the first.

If a substantive concern persists after the permitted remediation rounds, or
two lanes ran and remain in material conflict, stop before implementation
approval or `task.py start` and ask the user for judgment.

## 5. Completion report

Before leaving planning, report:

- changed artifacts and why the trigger applied;
- host review status;
- for each additional lane this repository defines, its status as completed,
  skipped, or failed -- omitting the line entirely when the repository defines
  none, since a lane that was never available was not skipped;
- each `C-*` concern and its final disposition;
- whether implementation is unblocked.
