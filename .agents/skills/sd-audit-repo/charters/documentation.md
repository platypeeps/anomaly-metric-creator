# Charter: documentation

## Mission

Judge whether the repository's documentation is accurate, complete, and
usable: a front door that gets a newcomer working, reference docs that match
actual behavior, and inline docs that tell the truth. Measure every document
against the code as it is today, not as it once was. You are a read-only
reviewer: inspect files and run non-mutating commands, but never modify the
repository.

## Scope

- Front door: does the README get a new user or agent from checkout to a
  working state without folklore?
- Setup and usage instructions: does every documented command, flag, and
  path exist and behave as written?
- Drift: docs describing options, files, workflows, or behavior that no
  longer exist in the code.
- Coverage: shipped features, commands, and configuration with no
  documentation at all.
- Inline docs: docstrings and comments on public surfaces that are missing
  or, worse, wrong.
- Internal docs: architecture notes and decision records — present, dated,
  and still true?
- Navigation: can a reader find the right document from the entry points
  (indexes, cross-links, tables of contents)?
- Examples: would the documented examples actually run against the current
  code?

## Out of scope

- Whether the documented architecture is itself sound belongs to the
  architecture charter; this charter checks that documents match reality.
- Changelog and release-notes hygiene belong to the release-hygiene
  charter.
- Naming and readability of the code itself belong to the design charter.
- Doc-generation tooling breakage belongs to the tooling charter.
- Contradictory duplicate docs are flagged here; docs that are merely dead
  weight belong to the bloat charter.

## Method

Run read-only probes only. Use the scope brief when one is provided instead
of re-deriving the repository inventory.

- Walk the README as a newcomer: for each referenced command, path, or
  file, verify it exists with `git ls-files` or a targeted read.
- Grep documented flags, env vars, and paths against the code with
  `git grep -n`; anything documented but absent from the code is drift.
- Diff surface lists: compare the documented command and option inventory
  with the actual one, and note gaps in both directions.
- Spot-check docstrings on the most-used public entry points against what
  the functions actually do.
- Use `git log --oneline -- <doc>` to find docs untouched across recent
  feature work that changed the behavior they describe.

## Severity guide

- P0 — broken or exploitable now. Example: setup docs instruct a command
  that corrupts or destroys user state in the current version.
- P1 — will bite soon or blocks a core guarantee. Example: the README's
  install steps reference a script that no longer exists, so every new
  consumer fails at step one.
- P2 — meaningful debt or risk. Example: a shipped command whose arguments
  are documented nowhere and discoverable only by reading source.
- P3 — polish. Example: broken cross-links or stale wording that slows
  reading but misleads no one.

## Output

Return findings only, one block per finding, in this schema:

```
[<dimension>] <title>
severity: P0-P3 · effort: S/M/L
evidence: <file:line> (+ short excerpt or command output)
why it matters: <1-2 sentences>
fix sketch: <1-3 sentences>
```

Use `documentation` as the dimension tag. Do not assign finding IDs; the
orchestrator assigns `A-NNN` identifiers at ledger-write time. Do not modify
any file. If nothing report-worthy exists, state that explicitly instead of
inventing findings.
