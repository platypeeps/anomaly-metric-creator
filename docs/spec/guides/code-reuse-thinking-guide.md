# Code Reuse Thinking Guide

## Search Before Creating

Before adding a helper, constant, parser branch, registry, fixture, command
renderer, or adapter rule, search for the existing owner and extend it when the
new behavior is the same contract. Sources:
`docs/spec/amc/backend/architecture.md`;
`docs/spec/amc/backend/testing-quality.md`; `src/anomaly_metric_creator/`;
`tests/`.

Prefer existing registries and helper APIs over parallel maps:
`COMPONENTS`, `SCENARIOS`, `DERIVATIONS`, `TOPOLOGY`,
`_EMIT_ARTIFACT_FILES`, `_COMBINE_OUTPUT_FILENAME`,
`_INSTANCE_DIMENSION_COLUMNS`, `resource_snapshot()`,
`trace_matches_search()`, and `unsupported_summary_from_traces()`. Sources:
`CLAUDE.md`; `src/anomaly_metric_creator/legacy.py`;
`src/anomaly_metric_creator/server_ops.py`;
`src/anomaly_metric_creator/server_traces.py`;
`src/anomaly_metric_creator/trace_bundle.py`; `tests/test_registry.py`;
`tests/test_server.py`; `tests/test_trace_bundle.py`.

## Duplication Checks

If the same untrusted payload field, JSON/JSONL record, command trace field,
config key, or schema field is parsed in more than one place, create or reuse a
single validator/normalizer/projection at the data boundary. Sources:
`docs/spec/amc/backend/api-cli-server.md`;
`docs/spec/amc/backend/operations-security-logging.md`;
`src/anomaly_metric_creator/server_traces.py`;
`src/anomaly_metric_creator/trace_bundle.py`;
`src/anomaly_metric_creator/server.py`; `tests/test_trace_bundle.py`;
`tests/test_server.py`.

When adding a new Kubernetes resource family, update all related aliases,
snapshot builders, renderers, API lists, object/table helpers, mutation overlay
handling, trace classification, and tests together. Sources:
`docs/spec/amc/backend/architecture.md`;
`src/anomaly_metric_creator/server_ops.py`;
`src/anomaly_metric_creator/server_mutations.py`;
`src/anomaly_metric_creator/server.py`; `tests/test_server.py`.

When adding a new scenario, metric, or component, update the canonical registry
and its documented/tested lockstep surfaces in the same change. Sources:
`docs/spec/amc/backend/scenarios-and-data.md`;
`src/anomaly_metric_creator/legacy.py`; `README.md`; `tests/conftest.py`;
`tests/test_scenarios.py`; `tests/test_registry.py`; `tests/test_server.py`.

## Adapter Rule

Keep platform directories as spec adapters. Do not copy durable project
rules into every Codex, Claude, Copilot, Gemini, or OpenCode file; put the rule
in `docs/spec/` and have platform files load or point to those specs. Sources:
`docs/spec/amc/backend/documentation-review.md`; `.agents/`; `.codex/`;
`.claude/`; `.gemini/`; `.github/`; `.opencode/`.

## Quick Checklist

- Did I search for an existing owner before adding a new helper or constant?
  Sources: `docs/spec/amc/backend/index.md`; `src/anomaly_metric_creator/`.
- Did I update the canonical registry/helper instead of creating a parallel
  map? Sources: `src/anomaly_metric_creator/legacy.py`;
  `src/anomaly_metric_creator/server_ops.py`; `tests/`.
- Did I reuse shared trace/search/config/schema validators instead of local
  casts? Sources: `src/anomaly_metric_creator/server_traces.py`;
  `src/anomaly_metric_creator/trace_bundle.py`; `tests/test_trace_bundle.py`.
- Did I add or update a `docs/spec/` citation for any new convention? Sources:
  `docs/spec/amc/backend/documentation-review.md`;
  `docs/work/archive/2026-06/2026-06-25-consolidate-agent-docs-trellis/prd.md`.
- Am I removing or replacing a re-export / alias / re-import block? Then check
  what it was *masking* before deleting it. A late `NAME = _other.NAME`
  reassignment silently overwrites an earlier duplicate definition of the same
  name in the same module, so the duplicate is invisible while the block
  stands and wins the moment it goes. Diff the module's attribute surface
  before and after — object identity, not just resolvability — rather than
  trusting a green suite, which passes either way while the values still
  agree. Found this way: two duplicate constant literals in
  `server.py` that the `server_ops` alias block had been correcting. Sources:
  `src/anomaly_metric_creator/server.py`; `tests/test_server_alias_surface.py`;
  `docs/spec/amc/backend/architecture.md`.
- Am I adding a guard that makes a module `__getattr__` refuse a *class* of
  names? Then `__dir__` owes the same predicate. A guard decides what can be
  read and `__dir__` decides what is advertised; write the condition twice and
  they drift, and `dir()` starts listing names that reading raises on. Extract
  one named predicate and call it from both. Found this way: `dir(server)`
  listing `server_ops.__all__` while `server.__all__` raised. Sources:
  `src/anomaly_metric_creator/server.py`; `tests/test_server_alias_surface.py`.
