# Local test split — Design (SD Work Designs, 2026-07-18)

## Overview

A bare `.venv/bin/pytest` runs the whole suite at `-n 4`, so each GB-scale
session fixture is rebuilt once per worker that touches a consuming file.
The CI split builds each exactly once. Developers should get that by
default.

## Correction to the PRD framing

The PRD implies the split is undocumented locally. It is not:
`docs/DEVELOPMENT_CYCLE.md:59-64` already gives it —

```bash
.venv/bin/pytest -n 0 -m heavy
.venv/bin/pytest -n 2 --dist loadfile -m "not heavy"
```

Two real problems remain, and they are narrower than "add the split":

1. It is gated behind the phrase *"For high-risk runtime changes"*, so the
   fast path reads as an occasional extra rather than the normal way to run
   the suite.
2. It hands a developer the **CI runner's** `-n 2` — correct for a 4-vCPU
   runner, wrong for a 14-core workstation, where `-n 4` is 36% faster.

## Proposal

**Fix the guidance, not just the numbers.** Invert the framing in
`docs/DEVELOPMENT_CYCLE.md`: the split is the normal full-suite command;
a bare `pytest` is the convenience path for a narrow subset. Use `-n 4` on
the light lane, and note that `-n` above 4 does not help because
`--dist loadfile` bounds parallelism by file count (measured: `-n 10` buys
3% over `-n 4`).

**Provide one command.** The two-line sequence is the thing developers skip.
Options, to choose during implementation:

- a `Makefile` / `justfile` target — needs a new tool or file in a repo that
  currently has neither;
- a small `scripts/` entry — consistent with existing repo tooling, but adds
  a script to maintain and would need classifier treatment (see
  `07-18-fix-ci-classifier-script-paths`);
- documentation only — zero new surface, relies on developers reading.

Recommend the `scripts/` entry, matching how this repo already packages
developer commands, and register it in the classifier as repo tooling in the
same change.

**Leave `addopts` alone.** `-n 4 --dist loadfile` is correct for the light
lane and for any narrow selection, which is what a bare `pytest` is usually
doing. Changing the default to `-n 0` would slow the common case to fix the
uncommon one. The fix is to make the split easy and documented, not to
pessimize the default.

**Correct the `pyproject.toml:67-81` comment.** It states `--dist loadfile`
bounds fixture instantiation to "at most one instantiation per file", which
is true but misleading — multiple files consume the same fixture, so the
real bound is `min(consuming files, workers)`:

| Fixture | Consuming files | Instantiations at `-n 4` |
|---|---|---|
| `n3_one_day_dataset_dir` | 4 | 4x |
| `seven_day_run` | 6 | 4x |
| `seven_day_schema_run` | 3 | 3x |
| `n3_seven_day_dataset_dir` | 2 | 2x |

The `~5 GB` budget at `:75` is `4 x 1.3 GB` — derived from the 1-day fixture
only, and from a size figure that is itself ~5x too high
(`07-18-fix-heavy-marker-and-fixture-docs` corrects it to 264 MB). Rewrite
the comment against measured numbers.

## Boundaries And Non-Goals

- No CI change (`07-18-perf-ci-worker-counts` owns those values).
- No change to the `heavy` marker or which tests it selects.
- `-n 0` stays documented as the required mode for `pdb`; `-n 1` still
  spawns a worker subprocess and breaks interactive debugging.

## Affected Files

`docs/DEVELOPMENT_CYCLE.md:59-64`, `pyproject.toml:67-81` (comment),
`CLAUDE.md` parallel-execution section, possibly a new `scripts/` entry plus
its classifier registration and `tests/test_ci_change_classifier.py` case.

## Risks And Edge Cases

- **A new script needs classifier treatment**, or it lands in the
  `app_required` bucket and triggers the full matrix on every edit — the
  exact bug `07-18-fix-ci-classifier-script-paths` exists to fix. Coordinate
  or sequence with it.
- **Machine-dependent worker counts do not belong hardcoded in docs.**
  Recommending `-n 4` on a 14-core box is right; on a 4-core laptop it is
  the whole machine. Phrase as "`-n 4` is the practical ceiling under
  `--dist loadfile`; lower it on smaller machines" rather than as a fixed
  number.
- **The split's heavy lane at `-n 2`** (259.79s, 11.25 GB peak) is
  comfortable at 48 GB but not on a 16 GB laptop. Document `-n 0` as the
  conservative heavy setting.

## Validation

- Time a bare `pytest` and the split sequence on the same machine; the split
  must be measurably faster. Record both.
- Confirm a bare `pytest` still passes the full suite — whatever the default
  becomes, nobody should be left with a broken plain invocation.
- If a script is added: confirm it classifies lightweight and that
  `tests/test_ci_change_classifier.py` covers it.
