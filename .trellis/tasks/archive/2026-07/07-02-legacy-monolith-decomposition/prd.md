# Decompose the 12.9k-line legacy.py monolith

## Audit context

- **Source:** first-time staff-engineer audit, 2026-07-02.
- **Confidence:** CONFIRMED (structural fact).
- **Severity:** HIGH as long-term maintainability debt; the single biggest
  "expensive later" decision in the repo. Not urgent — this is an epic.
- **Category:** design / architecture.
- **Systemic pattern:** the heavy lint + 14-point-checklist governance is
  *compensating* for this structural debt rather than paying it down.

## Goal

Incrementally break `src/anomaly_metric_creator/legacy.py` (~12,919 lines) into
focused, cohesive modules — without changing a single output byte — so that
future changes land in small files, type-check cleanly, and stop concentrating
risk in one 16×-over-cap file.

## Problem

`legacy.py` is one file holding: CLI parsing/reconciliation, the domain model
(`MetricSpec`/`Instance`/`Edge`/`Scenario`), the entire generation pipeline
(`generate_component`, `_natural_column`, topology coupling + saturation,
per-instance dispatch), all import-time validators, the combine writers (wide +
long), the gauge writer, `schema.json` writer + validator, OTLP payload builders
(JSON + protobuf), the OTEL streamers, and the header-redaction helpers.

It is ~16× the repo's own 800-line file cap (documented in the user's global
coding-style rules and CLAUDE.md's "many small files" guidance). The existing
package facades — `combine.py`, `models.py`, `otel.py`, `scenarios.py`,
`schema.py` — are 7-line re-exports (confirmed), explicitly described in
CLAUDE.md as "import-stability points for future splits, not parallel behavior
copies." This task is that future split.

## Constraints (non-negotiable)

- **Byte-identical output.** Every locked SHA-256 golden hash (default, N=3,
  7-day, gauges, schema, combine) must remain unchanged at every step. This is a
  pure move/re-export refactor, not a behavior change.
- **RNG draw order is load-bearing.** Moving code must not change the order or
  count of `RunContext.rng` draws. Any reordering risks the determinism
  contract — verify with the golden hashes after every extraction.
- **Public import surface preserved.** `anomaly-metric-creator.py` (the shim
  re-exports every public name) and existing `from ... import legacy` /
  `from ...combine import ...` call sites must keep working; the facades stay
  the stable import points.
- **No direct-spec-load test regressions.** The `amc-no-direct-spec-load` hook
  and session-scoped `amc` fixture assume a single importable module — keep the
  memoized `conftest._load_amc()` path valid.

## Requirements

- Write a `design.md` first: propose the target module boundaries (candidates,
  each already a section header in `legacy.py`): `generation.py`
  (`generate_component`/`_natural_column`), `topology.py` (edges, coupling,
  saturation, per-instance), `scenarios_impl.py` (SCENARIOS + validators),
  `combine_impl.py`, `gauges.py`, `schema_impl.py` + `validate.py`,
  `otlp.py` (payload builders), `otel_stream.py`, `cli_args.py`
  (`parse_args`/`_reconcile_cli_surface`), `redaction.py`. Sequence smallest /
  most-isolated first.
- Extract **one module per PR**, moving code and re-pointing the facade, running
  the full golden-hash suite after each. Prefer moving whole functions
  unchanged over editing them.
- Keep or migrate the import-time validators so they still run exactly once at
  package import (order matters for some — e.g. topology registries validate
  after `_TOPOLOGY_SATURATION_TARGETS`).
- Update CLAUDE.md's module map as each split lands (CLAUDE.md is the canonical
  source guide and will otherwise drift).

## Acceptance criteria (per extraction; the epic closes when legacy.py is thin)

- [x] After each extraction PR, all locked golden hashes pass unchanged.
- [x] The public import surface (shim + facades + test fixture) is unchanged;
      `tests/` pass under the existing xdist config.
- [x] Each new module is under the 800-line cap and has a single clear
      responsibility.
- [x] CLAUDE.md's architecture/module section is updated in the same PR.
- [x] Import-time validation still fires exactly once, in the same order.

## Status update (2026-07-06 review)

- Steps 1–7 landed (PRs #178, #181, #183, #185, #198, #203): `redaction.py`,
  `timeutil.py`+`otlp.py`, `csv_layout.py`+`gauges_impl.py`+`artifacts.py`,
  `combine_impl.py`, `schema_impl.py`+`validate_impl.py`, `otel_stream.py`.
  `legacy.py` is **9,188 lines** (from 12,992 at design time; the "~12,919"
  above was an earlier measurement of the same pre-extraction state).
- Steps 8–10 remain (`decomp-cli-args`, `decomp-catalog-data`,
  `decomp-generation-topology`), plus the `scenarios_impl.py` scheduling
  gap and the end-state decision now recorded in `design.md`'s Status
  section.
- Acceptance-criteria deviation to resolve: `validate_impl.py` shipped at
  1,684 lines (> the 800-line cap this epic's own criteria require) — see
  design.md Invariants note.

## Recovery status (2026-07-21)

All ten original child tasks are archived, but `07-02-decomp-catalog-data`
completed only its PR A (`models_impl.py` + `catalog.py`). Its own archived
plan and notes leave PR B (`scenarios_impl.py` + `scenario_catalog.py` and the
resolution cluster) undone, while `legacy.py` still measures 4,829 lines and
contains that roughly 3,300-line surface. The epic is therefore not 10/10
complete in substance.

Child `07-21-decomp-scenario-catalog-recovery` completed the omitted PR B with
a split that keeps executable modules below 800 lines and preserves one ordered
declarative registry. On 2026-07-21 the maintainer rejected the proposed final
dispatch-root waiver; child `07-21-decomp-legacy-dispatch-root` now owns the
remaining `main()` split and the epic cannot close until it merges with
unchanged hashes and `legacy.py` is below 800 lines.

Child `07-21-decomp-legacy-dispatch-root` merged as PR #291 on 2026-07-21.
`legacy.py` is now a 766-line compatibility/runtime-wiring facade, all newly
extracted behavior modules are below 800 lines, the full 1,700-test suite kept
the locked hashes unchanged, and the final-head remote heavy/light matrix plus
combined coverage and CodeQL gates passed. The no-waiver epic is complete.

## Notes

- **Epic — do not attempt in one session.** Break into per-module child tasks
  once `design.md` fixes the boundaries.
- Highest-leverage sequencing: extract the leaf/most-isolated surfaces first
  (redaction, OTLP builders, gauge writer) to build confidence in the
  golden-hash safety net before touching `generate_component`/topology (the
  RNG-order-sensitive core).
- This unblocks `07-02-ci-typecheck-and-coverage`: small modules type-check far
  more cleanly than a 12.9k-line file.
