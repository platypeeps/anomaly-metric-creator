---
title: Type the spec/config/server boundaries
status: planning
parked: 2026-09-01 age-sweep
created: 2026-07-17
---
# Type the spec/config/server boundaries

## Audit context

- **Source:** 2026-07-17 full repo audit @ b0df00b — report:
  `.trellis/audit/report-2026-07-17.md`; per-item evidence + fix sketches:
  `.trellis/audit/ledger.md`.
- **Ledger items:** A-005 (P2·M), A-006 (P2·M), A-007 (P2·M), A-008 (P2·S), A-002 (P2·M), A-009 (P3·S), A-010 (P3·S)

## Goal

The system's central boundaries are untyped: anomaly specs are mutated dicts with
hidden keys, generator dispatch is signature introspection, run config crosses the
server boundary as a Namespace with re-hardcoded defaults, and the server reaches
legacy internals through Any. Natural companion to decomposition steps 8-10 —
sequence after/with 07-02-decomp-cli-args and 07-02-decomp-catalog-data.

## Scope (ledger items)

- A-005 — frozen AnomalySpec/CascadeSpec with explicit provenance fields; _validate_scenario_spec becomes a pure parser (byte-identical output).
- A-006 — explicit generator-arity opt-in (wrappers or generator_args); introspection kept as deprecation shim; no RNG changes.
- A-007 — frozen RunConfig from _reconcile_cli_surface; delete getattr fallbacks or route through DEFAULT_* constants.
- A-008 — shared signal_stream_config(args) builder in otel_stream consumed by legacy.main and server._run_otel_streams.
- A-002 — import leaf-resident helpers directly in server_mcp/server_ops; typed Protocol for the genuinely-legacy surface (registries, _resolve_effective_specs, main).
- A-009 — keyword-only params on combine_logs_unified (or aligned order).
- A-010 — public aliases + __all__ at leaf modules; underscore names stay as compat bindings.

## Acceptance criteria

- [ ] All locked golden hashes unchanged at every step.
- [ ] server_mcp's mypy gate actually checks its generator-boundary calls.
- [ ] Sequenced against the decomposition epic without extraction conflicts.
- [ ] Closing PR flips each covered ledger item to `status: fixed` in
      `.trellis/audit/ledger.md` (same-PR, per ledger rules).
