---
title: Guard the repository to repomix-map direction (tracked files absent from the map)
status: planning
parked: 2026-09-01 bulk-park (D2)
created: 2026-08-17
---
# Guard the repository → repomix-map direction (tracked files absent from the map)

## Goal

Close the second half of the `docs/repomix-map.md` drift problem: a tracked
file that never appears in the map. `tools/check_repomix_map_freshness.py`
(task `08-17-repomix-map-freshness-lint`) covers the map → repository
direction; this covers the other one.

## Context

The map goes stale in two directions, and only one is currently guarded:

| drift direction | guarded by |
| --- | --- |
| the map lists a path that no longer exists | `tools/check_repomix_map_freshness.py` |
| a tracked file never appears in the map | **nothing** |

The unguarded direction is the **PR #382** class: six new `scripts/`
forwarders were added, never appeared in the map, and the merge gate caught it
after the fact.

It was deliberately excluded from the shipped guard rather than
half-implemented. The two directions cost very different amounts. An entry that
is in the map is by definition not excluded from it, so verifying it needs no
exclusion set at all — zero false positives by construction. Going the other
way requires knowing every rule that legitimately keeps a tracked file out, and
in this repository those rules come from three unrelated mechanisms (the
``--ignore`` flag now carrying two distinct exclusions):

| absent file | excluded by |
| --- | --- |
| `docs/repomix-map.md` | the explicit `--ignore` flag in `scripts/update_repomix` |
| everything under `.trellis/tasks/` | the same `--ignore` flag; added after the map's own freshness guard and the command pack's completion finalization proved unable to accept the same archive commit. This is now the **largest** excluded set — roughly half the paths the map used to carry — so a naive repository → map check reports every task file as missing |
| `.trellis/.template-hashes.json` | root `.gitignore` — yet the file is **tracked anyway**, so a plain `git check-ignore` reports no match; only `--no-index` finds it |
| `uv.lock` | repomix's **built-in default ignore patterns**, named in no file in this repository |

## The open decision

Where does the authoritative exclusion set come from? This is the whole task;
the comparison itself is trivial once it is answered.

Two of the three mechanisms are already solvable without new machinery:

- the `--ignore` set is recorded in the map's own Notes section
  (`Files matching these patterns are excluded: docs/repomix-map.md`), so it can
  be read from the artifact rather than by parsing `update_repomix`;
- the `.gitignore` set is derivable with `git check-ignore --no-index`.

The third is the blocker. Repomix's defaults live inside the tool. Reproducing
them means either depending on the `repomix` binary — which `scripts/update_repomix`
shows is not always present, since it exits `127` without it — or
hand-maintaining a mirror of an upstream list. A mirror is a second registry for
the same fact, drifting on every Repomix upgrade with no guard of its own:
exactly the failure mode the lint exists to prevent. Shipping that would trade a
known gap for a silent one.

Candidate resolutions to evaluate, none yet chosen:

- read the built-in defaults from the installed repomix package when it is
  present and **skip only that third mechanism's coverage** when it is not,
  reporting the reduced scope rather than passing silently;
- narrow the check to trees where the defaults provably do not apply (a
  positive allow-list of directories rather than a mirror of an exclusion list);
- treat "absent from the map" as a warning surface in the ship flow rather than
  a blocking lint.

## Requirements

- A tracked file absent from `docs/repomix-map.md` is reported.
- The three known-legitimate exclusions above are not reported.
- The exclusion set is **derived**, not hardcoded as a literal list beside the
  generator. If a mechanism cannot be derived, the guard states the resulting
  coverage gap rather than passing silently over it.
- Runs without the `repomix` binary, or degrades to explicitly-reported reduced
  coverage without it. It must not be skipped into uselessness in CI.
- Follows the repo lint contract: full module docstring carrying the contract,
  the `0` clean / `1` violation / `2` structural-error exit split, an acceptance
  test file, and CI lane coverage per `tools/check_guard_ci_coverage.py`.

## Non-goals

- Re-implementing the map → repository direction. That guard exists; extend or
  sit beside it rather than replacing it.
- Regenerating the map automatically. The remedy stays one explicit
  `./scripts/update_repomix`.

## Acceptance criteria

- [ ] The open decision above is resolved in writing, with the rejected options
      and their reasons recorded, before any implementation.
- [ ] A tracked file added without regenerating the map fails the guard —
      pinned by a fixture reproducing the PR #382 shape.
- [ ] The live repo passes with zero findings, with the three known exclusions
      silent.
- [ ] Any coverage the guard cannot derive is reported by the guard itself, not
      only documented in prose.
- [ ] `tools/check_guard_ci_coverage.py --list` stays clean and the
      "lints whose own tests never run in the QUICK lane" section prints `none`.
- [ ] The CLAUDE.md Repository lints table gains a row.

## Notes

Blocked on the exclusion-source decision, not on implementation effort. Worth a
`design.md` pass before writing code; the comparison is a few lines once the
derivation question is settled.

Related: `08-17-repomix-map-freshness-lint` (shipped the other direction), and
its `design.md` § D1, which records why this was split out.
