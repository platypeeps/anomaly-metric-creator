# CLAUDE.md

Project memory for the anomaly metric creator: a single Python package that
generates deterministic synthetic observability artifacts (per-component metric
CSVs, an anomaly manifest, logs, traces, gauges, a schema document) and can
serve them through an incident-simulator HTTP facade (`amc serve`) that answers
real `kubectl`, Helm, and MCP clients.

**Canonical development conventions live in
[`docs/spec/amc/backend/index.md`](docs/spec/amc/backend/index.md) and
the focused specs it maps.** This file is an adapter: it carries only the
always-needed orientation below plus routing. When a durable rule changes,
update the focused spec under `docs/spec/` first. User-facing usage, install, the CLI
reference, output files, and the anomaly catalog live in
[README.md](README.md) — read it first if you need to run the tool. The trust
model and remote-bind posture live in [SECURITY.md](SECURITY.md).

## Read this before touching that surface

| Touching… | Read |
| --- | --- |
| Generation, registries, module boundaries, topology | [architecture.md](docs/spec/amc/backend/architecture.md), [scenarios-and-data.md](docs/spec/amc/backend/scenarios-and-data.md) |
| CLI, server, API, schema, validation, trace bundles | [api-cli-server.md](docs/spec/amc/backend/api-cli-server.md) |
| Command traces, persistence, auth/CORS/rate limits, redaction, k8s/Helm facades, debug UI | [operations-security-logging.md](docs/spec/amc/backend/operations-security-logging.md) |
| Tests, validators, determinism, CI, dependencies, review readiness | [testing-quality.md](docs/spec/amc/backend/testing-quality.md) |
| Docs, PR descriptions, Copilot guidance, agent-platform files | [documentation-review.md](docs/spec/amc/backend/documentation-review.md) |
| Topology edges, per-edge tuning, per-instance routing | [docs/topology.md](docs/topology.md), [README.md](README.md#topology-graph-v1) |
| Dispatch order, subcommand flow, artifact lifecycle diagrams | [docs/application-flow.md](docs/application-flow.md) |
| Release process, pinned-tool bumps, review cadence | [docs/DEVELOPMENT_CYCLE.md](docs/DEVELOPMENT_CYCLE.md) |

## Module ownership map

`src/anomaly_metric_creator/legacy.py` is the historic public binding and
live-runtime wiring surface — a compatibility facade, not the behavior owner.
Edit the focused module for behavior changes; `legacy.py`, the top-level
`anomaly-metric-creator.py` shim, `cli.py`, and the small package facades
(`combine.py`, `models.py`, `otel.py`, `scenarios.py`, `schema.py`) are wiring
and import-stability surfaces only. `python anomaly-metric-creator.py …`, the
installed `amc` / `anomaly-metric-creator` console scripts, and the test suite
all drive the same code.

The facade exports are a **CLI-internal surface, not a supported programmatic
API.** `SystemExit` raised from an importable module, unconditional
combine-path stdout, and silently skipping a missing per-component CSV are
documented semantics — do not rework them into library-grade error handling,
and give a new facade export the same note. The single revisit trigger is a
real embedder requirement, which belongs inside the typed-boundaries audit
work. Full posture in
[api-cli-server.md](docs/spec/amc/backend/api-cli-server.md) §
Library-API Error Posture.

| Surface | Owner |
| --- | --- |
| Run orchestration, artifact lifecycle, output hygiene | `run_pipeline.py` (`main()`), `run_defaults.py` |
| `RunContext`, `MetricSpec`, `Instance`, instance-config loading | `models_impl.py` |
| Component / instance / metric registries | `catalog.py` |
| Column generation | `generation.py`, `generation_helpers.py`, `generation_derivations.py`, `generation_emit.py`, `anomaly_dispatch.py` |
| Scenario model, catalog, validation, runtime | `scenario_builders.py`, `scenario_catalog.py`, `scenario_validation.py`, `scenarios_impl.py` |
| Topology graph, coupling, saturation | `topology_models.py`, `topology_registry.py`, `topology_impl.py`, `topology_compose.py`, `topology_instances.py`, `topology_support.py` |
| CSV layout primitives + the one long-form merge writer | `csv_layout.py` |
| Artifact writers | `gauges_impl.py`, `combine_impl.py`, `schema_impl.py`, `artifacts.py` |
| Output validation | `validate_impl.py`, `validate_cells.py`, `validate_topology.py`, `validate_topology_instances.py` |
| OTEL / OTLP | `otel_stream.py`, `otlp.py`, `redaction.py` |
| CLI parsing and subcommands | `cli_args.py`, `cli_argv_safety.py`, `cli_subcommands.py`, `cli.py`, `version.py` |
| HTTP serve facade | `server.py` |
| `serve --config` loading, validation, serve-arg parsing | `server_config.py` |
| Simulation state, command rendering, snapshots | `server_ops.py` and its leaves (`server_ops_support.py`, `server_ops_parse.py`, `server_ops_profiles.py`, `server_ops_explain.py`, `server_ops_payloads.py`, `server_command_render.py`, `server_k8s_objects.py`, `server_k8s_tables.py`, `server_k8s_api.py`, `server_k8s_api_trace.py`, `server_helm_impl.py`) |
| Traces, overlay state, debug UI, MCP | `server_traces.py`, `server_mutations.py`, `server_debug_ui.py`, `server_mcp.py` |
| Offline bundle analysis | `trace_bundle.py` |

Full per-module contents, the server leaf DAG, and the import directions are in
[architecture.md](docs/spec/amc/backend/architecture.md) § Module
Boundaries.

`server.py` republishes the historic `anomaly_metric_creator.server` attribute
surface through a module `__getattr__` forwarding to `server_ops`, so **adding
a name to `server_ops` needs no alias line in `server.py`**. Two exceptions,
both pinned by `tests/test_server_alias_surface.py`: a `server_ops` name that
`server.py` reads as a *bare global* must still be imported explicitly (PEP 562
does not cover global-name resolution inside the module, so a delegated one
fails with a `NameError` on one request path), and `__dunder__` names are not
forwarded (`server_ops` has `__all__`; `server.py` must not inherit it).

## Area rules

These load from `.claude/rules/` when you touch matching paths.

| Topic | File |
| --- | --- |
| Extraction / re-import invariant, the 800-line module ratchet | [`.claude/rules/extraction.md`](.claude/rules/extraction.md) |
| Determinism contract, `generate_component()` pipeline order | [`.claude/rules/determinism.md`](.claude/rules/determinism.md) |
| Repository lints (`tools/check_*.py` guards) | [`.claude/rules/repository-lints.md`](.claude/rules/repository-lints.md) |

## Working rules

- **Import must not generate.** `main(argv=None)` is the entry point and is
  invoked only under `if __name__ == "__main__"`; importing the module must not
  write files.
- **One registry per fact.** No hand-rolled emit→filename, metric→component, or
  component→derivation maps beside `_EMIT_ARTIFACT_FILES`, `COMPONENTS`,
  `DERIVATIONS`, `TOPOLOGY`, `_COMBINE_OUTPUT_FILENAME`, or
  `_INSTANCE_DIMENSION_COLUMNS`. Dispatch tables raise on unknown keys —
  `table[key]`, never `table.get(key)`.
- **Serve mode is a facade,** not a second copy of generation behavior, and the
  Kubernetes/Helm/MCP surfaces read the one overlay-aware `resource_snapshot()`
  — never a second resource model.
- **Every artifact publishes atomically** through `_atomic_artifact_open` /
  `_atomic_write_text`; never `open(final_path, "w")`.
- **Eval-mode ground-truth wall.** `amc serve --mcp-eval-mode` is an evaluation
  target for incident-response agents, and the run's `anomalies.csv` plus
  active scenario slugs are the harness's scoring rubric. No rubric-bearing
  surface and **no active-scenario identifier** may reach any endpoint an eval
  agent can read — only observable symptoms. A new endpoint must be classified
  in the rubric or investigation registry in `server.py`, never left to default
  open.
- **`--instances-per-component 1` (the default) is the byte-identical path.** A
  single anonymous `Instance()` emits the legacy wide CSV shape; named or
  fanned-out instances switch to the dimension-aware long form. Preserve the
  N=1 path exactly.
- **`--instance-config` and `schema.json` are untrusted read-back
  boundaries** — validate shape and type on the reader side.
- **`serve --config` validates both sections at load.** `server` keys use the
  `_SERVE_CONFIG_SERVER_KEYS` allowlist; `generate` keys are validated by
  probe-parsing the config-derived argv through the real generate parser —
  never add a hand-maintained second list of generate keys, it drifts on every
  new flag.
- **`--inject-dst-artifact-day > 0` with multi-instance (or with gauge
  streaming) is an intentional design boundary**, not a gap — the DST splice
  and per-instance row blocks are non-monotonic along two independent axes.
  Keep the parse-time rejection and the `generate_component` defense-in-depth
  guard; never make it partially work in one artifact family. Full rationale
  in [api-cli-server.md](docs/spec/amc/backend/api-cli-server.md) § CLI
  Surface.
- Add a new `Instance` field by adding its name to
  `_INSTANCE_DIMENSION_COLUMNS`; the config validator and constructor both
  derive from it. The remaining lockstep sites are the README key list and
  `_validate_instance_list` if the field needs its own checks.
- Prefer a mechanical `tools/check_*.py` lint with tests over a prose rule
  whenever the pattern is greppable.

## Tests

Run with `.venv/bin/pytest` after installing the `dev` extra. Tests write only
into `tmp_path`, never `iot_logs/`. `pyproject.toml` pins
`addopts = "-ra --dist loadfile -n 4"`, so the default local run is parallel
across four xdist workers — this is the measured-fastest full-suite path. Use
`-n 0` for true in-process runs (`pdb`); `-n 1` still spawns a worker
subprocess. The `heavy` marker is **auto-applied** by
`pytest_collection_modifyitems` from the fixture closure — never hand-write it;
register a new GB-scale fixture in the appropriate frozenset in
`tests/conftest.py`.

Tests must stay order-independent and file-isolated: no cross-file shared
mutable state (module-level caches, filesystem fixtures outside `tmp_path`,
environment variables set without `monkeypatch`), or xdist will distribute them
to different workers and produce non-reproducible failures. Derive scenario
coverage from `amc.SCENARIOS` rather than hard-coding slug lists. Full
conventions — fixture reuse, streaming reads, resource cost, cross-platform
guards, the CI partition contract — are in
[testing-quality.md](docs/spec/amc/backend/testing-quality.md).

## Review readiness

The 15 pre-PR checklist headings and their per-heading bullets are canonical in
[testing-quality.md](docs/spec/amc/backend/testing-quality.md) § Review
Checklist, mirrored by `.github/PULL_REQUEST_TEMPLATE.md` and
`.github/instructions/anomaly-metric-creator.instructions.md` and enforced by
`tools/check_copilot_instruction_contract.py`. Rename a heading in the spec and
update every mirror in the same diff. PRs open as draft and walk the checklist
before draft status is removed.

Doc/comment-vs-code drift is the most-flagged review pattern in this repo's
history: when you change a default, a count, an edge list, or a dispatch order,
grep the **old value** across docstrings, CLI help, `README.md`, `docs/`, and
the specs — not only the file you edited.

Known Copilot false positives are catalogued in
[testing-quality.md](docs/spec/amc/backend/testing-quality.md); verify a
flag against current `HEAD` before acting, but treat flags as actionable by
default.

## Local gates

```bash
.venv/bin/pytest                       # parallel full suite
.venv/bin/pre-commit run --all-files   # lints, ruff, mechanical guards
.venv/bin/ruff check tests/
git diff --check
node scripts/check-review-preflight.mjs        # the local review gate
make check                             # all of the above plus every CI Result step, in .venv-check
```

Run the narrowest focused regression first, then affected suites, then broader
checks when the blast radius warrants it. The local gate is the merge gate
(`repo.ci=local`): `sd-ship merge` runs `make check` and posts `sd/local-gate`,
the one required check. The CI workflows are disabled on purpose; do not
re-enable them to get a missing check. See
[testing-quality.md](docs/spec/amc/backend/testing-quality.md) for the lane
classification, the heavy/light partition, and the coverage and mypy gates.
