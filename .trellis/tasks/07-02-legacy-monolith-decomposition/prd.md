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

- [ ] After each extraction PR, all locked golden hashes pass unchanged.
- [ ] The public import surface (shim + facades + test fixture) is unchanged;
      `tests/` pass under the existing xdist config.
- [ ] Each new module is under the 800-line cap and has a single clear
      responsibility.
- [ ] CLAUDE.md's architecture/module section is updated in the same PR.
- [ ] Import-time validation still fires exactly once, in the same order.

## Notes

- **Epic — do not attempt in one session.** Break into per-module child tasks
  once `design.md` fixes the boundaries.
- Highest-leverage sequencing: extract the leaf/most-isolated surfaces first
  (redaction, OTLP builders, gauge writer) to build confidence in the
  golden-hash safety net before touching `generate_component`/topology (the
  RNG-order-sensitive core).
- This unblocks `07-02-ci-typecheck-and-coverage`: small modules type-check far
  more cleanly than a 12.9k-line file.
