---
name: sd-test-gaps
description: Use when the user wants to close the worst per-file coverage gaps by ranking shipped files by coverage and authoring focused tests for the top offenders through the normal implement/check flow.
---

# SD Test Gaps

Run this project-local skill for `sd-test-gaps` and `/sd:test-gaps` style
work. It closes the worst coverage gaps with targeted tests: run the repo's
documented coverage flow as the baseline, rank shipped files by per-file
coverage, author focused tests for the worst-covered files, and report the
per-file before/after movement.

This command writes test files and fixtures only. It never edits product
code, never changes coverage configuration, and never lowers a configured
coverage floor.

## When to use

Run this command when per-file coverage holes need closing: after an audit
or a per-file coverage gate flags files far below the aggregate floor,
before tightening a configured floor, or as recurring hygiene after a large
development stream leaves thin spots behind. Pass `file=<path>` when one
specific file is the known offender.

It complements `sd-full-check` (the gate that proves configured floors
hold): this command is the remediation loop that raises weak files toward
those floors, and `sd-audit-repo`'s testing dimension is a common source
of its gap list. It is not a debugging command — the baseline suite must
pass before any gap work starts.

## Arguments

Arguments arrive as free text with the invocation. Parse recognized
`key=value` arguments before treating a remaining bare non-option value as the
positional primary subject. Unknown option-shaped arguments are an error, not
a silent skip: stop and report them before running the baseline. This command
reads no environment variables; arguments are the only tuning surface.

- `file=<path>` — skip the ranking and close gaps in exactly this one
  shipped file. Error if the path does not appear in the coverage report.
- `max-gaps=N` — how many worst-covered files to work, default `3`.
  Ignored when `file=` is passed.
- One bare value is a target file path. `sd-test-gaps scripts/example.py` is
  equivalent to `file=scripts/example.py`; preserve a quoted path containing
  spaces as one path and apply the same coverage-report validation as `file=`.

Reject a positional file combined with `file=` before running the baseline.
Keep `max-gaps=` explicit and preserve its existing interaction with a selected
file. Reject additional bare values and unknown option-shaped input.

## Workflow

The flow is fixed: baseline → parse → rank → author → re-measure →
report. Never reorder it, never skip the baseline, and run every command
from the repository root.

1. **Baseline** — run the repo's documented coverage flow: `make test` when
   the Makefile wires coverage there, otherwise the flow the repo's docs
   name. Record the overall totals. If the run is red, abort: a failing
   baseline means fix-first, so stop and report the failure instead of
   writing any tests — abort if the baseline coverage run fails.
2. **Parse** — read the per-file coverage report the baseline produced
   (terminal table, XML, or JSON — whatever the repo's flow emits). If the
   flow produces no per-file breakdown, stop and report that the repo's
   coverage wiring lacks per-file output; never invent numbers.
3. **Rank** — order shipped files by coverage ascending. Exclude test
   files, fixtures, and generated files from the ranking; only product
   code counts as a gap. With `file=<path>`, target only that file and
   skip the ranking.
4. **Author** — for each of the top `max-gaps` files (default 3), one file
   at a time:
   - Read the uncovered ranges from the report, then read the product code
     around them to understand the untested behavior.
   - Write focused tests that exercise those ranges through the repo's
     normal implement/check flow, with the same rigor as feature work:
     assert on behavior, not on implementation details.
   - Add fixtures only when the new tests need them.
   - Place new tests per the repo's existing test layout and naming
     conventions; extend the file's existing test module when one exists.
5. **Re-measure** — re-run the coverage flow. Confirm every targeted file
   improved, the suite is green, and no other file regressed. If a
   targeted file failed to improve, report the miss honestly instead of
   widening scope past `max-gaps`.
6. **Report** — build the per-file before/after table and list the next
   worst gaps that remain unworked.

## Safety rules

- Write test files and fixtures only — never product code. If closing a
  gap appears to require a product-code change (dead code, an unreachable
  branch, or a bug the new test exposes), stop work on that file and
  report the finding instead of changing product code.
- Abort if the baseline coverage run fails. Never author tests on top of a
  red suite.
- Never lower configured coverage floors, and never edit coverage
  configuration, exclusion lists, or pragmas to make numbers pass.
- Never mark tests skipped, expected-failure, or excluded to inflate
  coverage numbers; every added test must run and assert.
- Keep added tests deterministic: no network access, no timing sleeps, no
  test-order dependence. A flaky test is worse than the gap it closes.
- Unknown argument names stop the run before the baseline.
- Do not stage, commit, push, or open pull requests. Leave committing the
  new tests to the user's normal commit flow, and report every added file
  as a changed file.

## Final report

The assistant's final response is mandatory-shaped: every item below
appears in every run, and an empty item states its emptiness explicitly —
write `none` rather than dropping the line. Keep it scannable: bullets and
short lines, one point per line, no paragraph blobs.

- Baseline: whether the baseline coverage run passed, plus the overall
  coverage totals before any gap work.
- Per-gap before/after table — one line per worked file, in the order
  worked:

```
<file> · <before%> · <after%>
```

- Test files added: every new or extended test and fixture file, one per
  line.
- Remaining worst gaps: the next shipped files by ascending coverage that
  were not worked this run, with their current percentages.
