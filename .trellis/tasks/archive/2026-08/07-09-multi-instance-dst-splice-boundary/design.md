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
  gauge streaming). *(Corrected 2026-08-26: this bullet originally cited
  CLAUDE.md as already documenting the design basis. It does not — see
  step 1. The nearest existing statement is `api-cli-server.md` on
  `_layout_allows_break`, which records that DST-injected runs and
  dim-aware per-instance-block runs are each non-monotonic — the two
  axes this decision refuses to combine.)*
- Demand is zero: the DST splice is a niche artifact-realism feature;
  no workshop/eval flow has asked for it under fan-out.

**Keep-unsupported arm (the work):**

1. **Settle the language** (PRD notes both "intentional boundary" and
   "only remaining gate" appear): standardize on **"intentional design
   boundary"**.

   *Corrected 2026-08-26 — the edit-site inventory this step originally
   carried (README flag docs, "CLAUDE.md (two sites: the gauges section
   + the multi-instance section)", and "the relevant
   `.trellis/spec/amc/backend/` file") was written against the
   pre-slimming tree and is wrong at HEAD.* The sweep is **substitutive
   in `README.md` only** and **additive everywhere else**:
   - `README.md` — three sites carry the old mixed language: the
     `--instances-per-component` row ("The only remaining gate is the
     intentional ... boundary"), the `--instance-config` row ("only the
     intentional ... boundary remains rejected"), and the gauge-streaming
     bullet ("intentionally incompatible").
   - `CLAUDE.md` — **no DST text exists** (at the time this was written,
     `grep -in "dst" CLAUDE.md` returned nothing; this task adds the
     bullet); CLAUDE.md is now a slim adapter. There is no
     "gauges section" and no "multi-instance section" paragraph to edit.
     The posture must be *added*, and per CLAUDE.md's own routing rule
     ("update the focused Trellis spec first") it lands there only as a
     short adapter line.
   - `.trellis/spec/amc/backend/` — **no file carries the posture**
     (at the time this was written, `grep -rn "inject.dst" .trellis/spec/`
     returned nothing; this task's own sweep is what changes that). The home
     is `api-cli-server.md` § CLI Surface, which owns flag-interaction
     and parse-time validation rules per CLAUDE.md's routing table.
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

`README.md` (three sites, substitutive),
`.trellis/spec/amc/backend/api-cli-server.md` (§ CLI Surface, additive —
the posture has no spec home today), `CLAUDE.md` (additive adapter line),
PRD (decision record). **Not**
`tests/test_args.py`/`tests/test_instances_per_component.py`: the
coverage grep run 2026-08-26 found both parse paths and the
`generate_component` guard already covered, so no test edit is needed.
No production file changes.

## Risks And Edge Cases

- *(Corrected 2026-08-26.)* This risk originally named load-bearing
  CLAUDE.md paragraphs; those paragraphs do not exist at HEAD, so there
  is nothing there to disturb. The live risk moved to `README.md`, whose
  three sites sit inside dense single-cell flag-table rows — the
  substitution must not reflow, truncate, or break the surrounding cell.
  `src/anomaly_metric_creator.egg-info/PKG-INFO` carries a stale
  build-artifact copy of the README wording; it is generated and must not
  be hand-edited.

## Validation

- Guard tests green (existing + any topped-up cases); doc grep shows
  the standardized phrase at all sites and the old mixed language gone;
  full suite untouched otherwise.
