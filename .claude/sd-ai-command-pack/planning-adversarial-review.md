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

## 2. Parallel review lanes

The host must perform its own adversarial review of the changed artifact set.
Challenge requirements, assumptions, design fit, failure modes, security,
operability, validation, rollout, rollback, and whether `implement.md` closes
the commitments in `prd.md` and `design.md`. Verify claims against repository
code, specs, and task context rather than accepting either review lane at face
value.

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

On Claude Code, capability-check the optional native Codex lane with both
`command -v codex` and `codex exec --help`. When both succeed, launch one
review-only `codex exec` command in a separate background Bash task before
starting the host review. Use:

- `--cd <repo-root>`
- `--sandbox read-only`
- `--ephemeral`
- **`< /dev/null`** — required; see below
- a focused prompt naming the active task directory and the changed planning
  artifacts, requiring evidence-backed concerns only and forbidding writes

**Redirect stdin from `/dev/null`.** In a background Bash task stdin is not a
TTY, so `codex exec` treats it as piped input, prints `Reading additional input
from stdin...`, and blocks forever waiting for a write that never comes. It
consumes no CPU while hung — observed at 0:00.07 CPU over 29 minutes — so it
looks like a slow review rather than a stuck one, and it produces no output at
all because output is fully buffered until the run ends. Without the redirect
this lane cannot complete in the background task this section requires, and the
failure mimics a timeout. With it, a normal review finishes in roughly 5-10
minutes.

Do not diagnose this as a missing CLI, an auth problem, or an oversized prompt.
A foreground `codex exec` succeeds under the same configuration, so a working
foreground probe does not clear the background lane.

Consider `-o <file>` (`--output-last-message`) to capture the final review text
directly instead of parsing it out of the full transcript.

Retain the task ID and collect the result with `BashOutput` even if the host
lane finds blockers. The host and Codex reviews should overlap; do not wait for
Codex before beginning the host review.

This lane uses the installed `codex` CLI directly. Do not inspect, install,
patch, or invoke the OpenAI Codex Claude plugin, its cache, a companion script,
or `/codex:adversarial-review`.

If the executable is missing, the help probe is incompatible, authentication
is unavailable, or execution fails, report `Codex: skipped` or `Codex: failed`
with the concrete reason. Continue the host review and planning convergence.
Never describe the skipped or failed lane as approval, and never make the
plugin a fallback dependency.

Before reporting `Codex: failed` for a run that hung or returned nothing, check
the stdin redirect above and check the process's accumulated CPU time. A hang at
near-zero CPU is the stdin trap, not a failed review — rerun it correctly rather
than degrading the convergence to a single lane. Reporting a lane failed when it
was never actually invoked correctly is worse than reporting it skipped: it
records an absent second opinion as an attempted one.

## 3. Concern disposition

Merge and deduplicate both lanes into one concern ledger. Assign stable IDs
`C-1`, `C-2`, and so on. For every material concern record:

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

When addressed concerns change a planning artifact, rerun the host review and,
only if it was available in the initial round, one fresh Codex review against
the updated artifact set. Reconcile each remediation round through the same
ledger. Run at most two remediation rounds (three automatic rounds total); do
not start a fourth automatic round.

If a substantive concern persists after the permitted remediation rounds, or
the two lanes remain in material conflict, stop before implementation approval
or `task.py start` and ask the user for judgment.

## 5. Completion report

Before leaving planning, report:

- changed artifacts and why the trigger applied;
- host review status;
- Codex status as completed, skipped, or failed;
- each `C-*` concern and its final disposition;
- whether implementation is unblocked.
