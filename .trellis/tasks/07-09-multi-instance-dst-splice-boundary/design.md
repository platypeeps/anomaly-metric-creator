# Multi-instance DST splice boundary — Design (SD Work Designs, 2026-07-17)

## Overview

Decision task: `--inject-dst-artifact-day > 0` is mutually exclusive
with `--instances-per-component > 1` / `--instance-config` (parse-time
gate + `generate_component` defense-in-depth ValueError). The question
is keep-unsupported vs implement a non-monotonic timestamp model.

## Proposal

**Recommendation: keep unsupported** (maintainer confirmation is the
task-start gate). The grounds are structural, not effort-shy:

- The long-form CSV writes per-instance row *blocks*; splicing the DST
  hour per block surfaces non-monotonic timestamps inside every block,
  which the `heapq.merge` consumers (`gauges.csv`,
  `combined_metrics_unified.csv`) cannot resolve — the same reason DST
  is already exclusive with the gauge paths even at N=1. Supporting the
  combo means a new non-monotonic batching model across six artifact
  families (wide CSV, long-form, gauges, combine, schema/validate, OTEL
  gauge streaming) — CLAUDE.md already documents this as the boundary's
  design basis.
- Demand is zero: the DST splice is a niche artifact-realism feature;
  no workshop/eval flow has asked for it under fan-out.

**Keep-unsupported arm (the work):**

1. **Settle the language** (PRD notes both "intentional boundary" and
   "only remaining gate" appear): standardize on **"intentional design
   boundary"** everywhere — README flag docs, CLAUDE.md (two sites:
   the gauges section + the multi-instance section), and the relevant
   `.trellis/spec/amc/backend/` file.
2. **Verify guard coverage** and top up only if thin: parse-time
   rejection for BOTH flag paths (`--instances-per-component N>1` and
   `--instance-config`) each × DST, message naming the active flag and
   pointing at `--inject-dst-artifact-day 0`; plus the
   `generate_component` ValueError for direct callers. Grep the suite
   first — add only the cases actually missing (the PRD's
   "refresh tests only if under-covered").
3. Record the decision + rationale in the PRD (acceptance bullet 1).

**Implement arm (if the maintainer overrides):** not specified here by
design — it requires its own full design (the non-monotonic model
touches every ordering assumption CLAUDE.md documents); this task would
then convert to that design effort. No partial support in one artifact
family (PRD hard rule).

## Boundaries And Non-Goals

- No behavior change under the recommendation; no guard relaxation; no
  duplicate task for the same boundary (PRD acceptance).

## Affected Files

README.md, CLAUDE.md (two sites), one spec file, possibly
`tests/test_args.py`/`tests/test_instances_per_component.py` (only if
coverage gaps found), PRD (decision record).

## Risks And Edge Cases

- The doc sweep must not disturb the load-bearing CLAUDE.md paragraphs
  that other tasks cite (gauges exclusivity, `_splice_dst_artifact`
  history) — wording-only edits, grep-verified.

## Validation

- Guard tests green (existing + any topped-up cases); doc grep shows
  the standardized phrase at all sites and the old mixed language gone;
  full suite untouched otherwise.
