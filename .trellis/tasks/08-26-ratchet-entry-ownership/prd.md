# Give every ratchet entry an owner or an exemption

## Goal

`tools/check_module_size.py` already states the rule, in its own docstring:

> Reasons in `RATCHET` are required and are printed by `--list`. Keep them
> pointing at the owning epic, so an enrolled module is traceable to the work
> that will remove it.

Five of the seven entries do not. They say "not yet decomposed" and name no
task, so the guard enforces debt that nobody owns and that no epic will ever
retire. The rule is prose, `reason` is never read by `analyse()` — only printed
by `--list` — and the drift is total in the direction that matters.

This is the same shape as `08-26-doc-drift-lint` and the same remedy
`CLAUDE.md` prescribes: prefer a mechanical `tools/check_*.py` lint over a
prose rule whenever the pattern is greppable.

## The evidence

The enrolled table as of this filing, by whether the reason names an owner:

**Owned — one entry:**

- `server_ops.py` 4424, `debt: 07-06-server-ops-decomposition`. That epic
  exists, is active, and says explicitly that it "stays open until the file is
  under the cap." This is the shape the docstring asks for.

**Permanently exempt — one entry:**

- `scenario_catalog.py` 2030, `permanent: one ordered data-only registry`. Not
  debt, and correctly not pointed at a task.

**Orphaned — five entries, all reading "not yet decomposed":**

- `server.py` 1978 — HTTP serve facade. Its reason is the longest in the table
  and narrates three prior ceiling changes, but the "server.py decomposition
  follow-up" it twice defers work to does not exist as a task.
- `server_mcp.py` 1453 — MCP surface.
- `server_debug_ui.py` 1194 — debug UI, most of it an embedded HTML/JS
  template.
- `server_traces.py` 1086 — trace and overlay state.
- `cli_args.py` 960 — CLI parser, 160 lines over the cap and the closest to
  clearing it.

`07-06-server-ops-decomposition` names four of those five, but as constraints
its extractions must not break — "facades, `server.py`'s alias block, and
`server_mcp.py`'s imports must all keep resolving" — not as scope. Its goal
sentence is `server_ops.py` alone. A grep for the module name finds those
mentions and reads them as ownership; they are not.

## Requirements

- Decide a disposition for each of the five, and record it in that entry's
  reason. Two dispositions exist today and the decision is which one applies:
  `debt: <active-task>` or `permanent: <why this file is not behavior>`.
  Inventing a third is in scope only if neither fits and the reason why is
  recorded.
- File a follow-up task for every module that gets a `debt:` verdict, before
  the reason cites it. A reason pointing at a task that does not exist is the
  defect this task closes, not a form of it.
- `server_debug_ui.py` is the one plausible `permanent:` candidate on the
  `scenario_catalog.py` precedent — a mostly-declarative embedded template
  rather than behavior. Evaluate it; do not assume it.
- `cli_args.py` at 960 is 160 over the cap and may clear it with one
  extraction, which would delete its entry outright rather than assign it an
  owner. Check that before writing a PRD for it.
- A deleted entry takes its rationale with it, so record every disposition —
  including "clears the cap, entry removed" — in this task's `design.md`, not
  only in the `RATCHET` reason. `design.md` is the technical decision record
  and is archived with the task, so a per-module table there survives the
  entry's deletion. The reason field is the *live*
  statement of who owns a module today; it cannot also be the history of why a
  module stopped being enrolled, because the guard's stale-entry rule requires
  deleting it. Anyone later asking why `cli_args.py` is not in the table needs
  somewhere to read the answer.
- Make the class mechanically impossible to recreate: `analyse()` must
  validate the reason, not just carry it.
- A malformed or orphaned reason exits `1`, not `2`. `2` is reserved for
  `StructuralError` — the repository is not shaped the way the lint can read,
  as when the package directory is missing. `RATCHET` is source inside the lint
  itself, its remedy is a one-line edit by whoever is already in the diff, and
  it is the same family as the existing stale-entry rule, which is also a
  violation rather than a structural error. Keep `2` meaning "cannot check"
  rather than "checked, and the table is wrong".
- Specify the marker grammar and anchor it, or the check passes on text that
  merely looks right. "Starts with `permanent:` or mentions a task" is not a
  rule — `debt:` with no slug after it, a slug that matches an existing
  directory only as a substring, two slugs where one is archived, or the word
  `permanent` appearing mid-sentence would all slip through. Require an
  anchored prefix, exactly one owner token for `debt:`, and resolution by exact
  directory name rather than substring search. Concretely, the grammar to
  implement:

  - The reason begins at index `0` with `permanent: ` or `debt: `. Not
    contains, not after leading whitespace — at index `0`.
  - After `permanent: `, the remainder is free prose saying why the file is not
    behavior. Its shape is not validated, but it must be non-empty after
    stripping whitespace — `permanent:` followed by three spaces is a bare
    `permanent:`, not a rationale. A bare one is the loudest possible unowned
    entry, since a permanent
    exemption is the one disposition that never expires and so is the one that
    most needs its reason on the record.
  - After `debt: `, the owner token is the run of non-whitespace characters up
    to the first `,`, `;`, or end of string. Exactly one such token; whitespace
    inside it is a violation, not a second owner.
  - That token must equal, by exact string comparison, the name of a directory
    directly under `.trellis/tasks/`. Not a path, not a prefix, not a match
    against the archive.
  - Everything after the terminator is free prose, and it may name other
    tasks. Exactly one owner is authoritative — the token before the
    terminator — and the guard must not scan the prose for slugs. This is not
    a loophole to close but existing, correct data to preserve: `server.py`'s
    reason today names three other tasks, as the history of its ceiling
    changes: `08-15-server-alias-getattr-delegation`,
    `06-29-persisted-server-mutation-state`, and
    `07-02-config-generate-key-validation`. None of them owns the module. A
    guard treating any slug in the reason as an ownership claim would read
    that entry as owned by three
    archived tasks, which is the false-ownership reading this task exists to
    rule out.

  Both existing well-formed entries already fit — `permanent: one ordered
  data-only registry…` and `debt: 07-06-server-ops-decomposition, extracting
  leaves…` — so this is being written down, not invented. Test each malformed
  shape above.
- Resolve the coupling question that guard creates, and record the answer.
  `check_module_size.py` is stdlib-only and reads nothing but
  `src/anomaly_metric_creator/`. Validating a task reference makes a source
  lint depend on `.trellis/` layout. Either accept that and say why, or find a
  form that does not — a `# noqa`-style marker, a sibling registry, an
  `--check-owners` mode that only CI runs.
- An owning task that archives while its module is still over the cap must
  fail the guard. That is the intended signal, not a false positive: the epic
  convention is that a decomposition task stays open until its module clears
  the cap, so an archived owner plus an enrolled module means the entry was
  orphaned again. Say so in the docstring so a future reader does not "fix" it
  by pointing the check at the archive.

## Acceptance Criteria

- [ ] Each of the five orphaned entries carries a recorded disposition, and the
      rationale for each is written down — including any module whose verdict
      is that it clears the cap instead.
- [ ] Every `debt:` reason names a task directory that exists and is active.
- [ ] `analyse()` rejects an orphaned reason with exit `1`, covered by a test
      per case: no marker; a bare `debt:` with no owner token; a `permanent:` whose
      rationale is empty or whitespace-only; a `debt:` naming a nonexistent task; a
      `debt:` naming an archived one; a slug matching an existing directory
      only as a substring; whitespace inside the owner token; and `permanent`
      appearing mid-sentence rather than as the anchored prefix.
- [ ] A well-formed `debt:` reason whose free prose names other, non-owning
      tasks is accepted, pinned by a test built from `server.py`'s live reason.
- [ ] Exit codes are asserted, not assumed: `0` on the clean live tree, `1` for
      each rejection above, and `2` still only for `StructuralError`.
- [ ] Every disposition is recorded in this task's archived record, including
      any module whose entry was deleted rather than assigned an owner.
- [ ] The guard passes on the live tree, and a live-tree test pins that.
- [ ] The coupling decision is recorded in the module docstring, with the
      archived-owner rule stated so it is not later mistaken for a bug.
- [ ] `tools/check_guard_ci_coverage.py` passes; the lint's watched files still
      select the lanes it runs in after any `files:` change.

## Notes

- Source: backlog survey, 2026-08-26. Found while answering which enrolled
  modules had no owning task, after `07-06-server-ops-decomposition` was
  observed at 9/9 children done with `server_ops.py` still 5.5× the cap.
- Sibling in shape to `08-26-doc-drift-lint`: a prose rule stated in the right
  place, violated by the majority of the cases it governs, with nothing running
  it. Coordinate the two if both are in flight — neither should invent a second
  convention for how a guard reports an unowned reference.
- Priority P2 rather than P3 because the cost is a handful of decisions plus a
  small guard, and the entries silently re-authorize between 960 and 1978 lines
  each with no path to ever being removed.
