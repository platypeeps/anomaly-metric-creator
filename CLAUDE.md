# CLAUDE.md

Project memory for the anomaly metric creator: a single Python package that
generates deterministic synthetic observability artifacts (per-component metric
CSVs, an anomaly manifest, logs, traces, gauges, a schema document) and can
serve them through an incident-simulator HTTP facade (`amc serve`) that answers
real `kubectl`, Helm, and MCP clients.

**Canonical development conventions live in
[`.trellis/spec/amc/backend/index.md`](.trellis/spec/amc/backend/index.md) and
the focused specs it maps.** This file is an adapter: it carries only the
always-needed orientation below plus routing. When a durable rule changes,
update the focused Trellis spec first. User-facing usage, install, the CLI
reference, output files, and the anomaly catalog live in
[README.md](README.md) — read it first if you need to run the tool. The trust
model and remote-bind posture live in [SECURITY.md](SECURITY.md).

## Read this before touching that surface

| Touching… | Read |
| --- | --- |
| Generation, registries, module boundaries, topology | [architecture.md](.trellis/spec/amc/backend/architecture.md), [scenarios-and-data.md](.trellis/spec/amc/backend/scenarios-and-data.md) |
| CLI, server, API, schema, validation, trace bundles | [api-cli-server.md](.trellis/spec/amc/backend/api-cli-server.md) |
| Command traces, persistence, auth/CORS/rate limits, redaction, k8s/Helm facades, debug UI | [operations-security-logging.md](.trellis/spec/amc/backend/operations-security-logging.md) |
| Tests, validators, determinism, CI, dependencies, review readiness | [testing-quality.md](.trellis/spec/amc/backend/testing-quality.md) |
| Docs, PR descriptions, Copilot guidance, agent-platform files | [documentation-review.md](.trellis/spec/amc/backend/documentation-review.md) |
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
| CLI parsing and subcommands | `cli_args.py`, `cli_subcommands.py`, `cli.py`, `version.py` |
| HTTP serve facade | `server.py` |
| Simulation state, command rendering, snapshots | `server_ops.py` and its leaves (`server_ops_support.py`, `server_ops_parse.py`, `server_ops_profiles.py`, `server_ops_explain.py`, `server_ops_payloads.py`, `server_command_render.py`, `server_k8s_objects.py`, `server_k8s_tables.py`, `server_k8s_api.py`, `server_k8s_api_trace.py`, `server_helm_impl.py`) |
| Traces, overlay state, debug UI, MCP | `server_traces.py`, `server_mutations.py`, `server_debug_ui.py`, `server_mcp.py` |
| Offline bundle analysis | `trace_bundle.py` |

Full per-module contents, the server leaf DAG, and the import directions are in
[architecture.md](.trellis/spec/amc/backend/architecture.md) § Module
Boundaries.

## Extraction / re-import invariant

The `07-02-legacy-monolith-decomposition` epic moves code out of `legacy.py`
under a fixed pattern. Follow it exactly:

- Code moves **verbatim**. `legacy.py` re-imports every moved name at the same
  conceptual location, so the historic `legacy.<name>` surface (shim, facades,
  tests, server `state.legacy` lookups) is unchanged.
- **New modules never import `legacy`** — the dependency direction is one-way.
  When an extracted module must read a registry still owned by `legacy.py`,
  `legacy.py` configures a **named, weak-referenceable live callback** and the
  leaf reads the current registry view through it. Named and weak-referenceable
  matters: an isolated `legacy.py` test load must stay garbage-collectable.
  Never snapshot a registry at import time and never add a reverse import.
- Callers move with the code. `_wide_component_rows_are_monotonic` is called
  only by `combine_logs_unified` in `combine_impl`, so a test stubbing the
  pre-scan patches `anomaly_metric_creator.combine_impl.<name>` — not the
  `legacy` re-import, because the intra-module call resolves in
  `combine_impl`'s namespace.
- Import-time validation stays at its historical `legacy.py` call site even
  when the validator implementation moves, so validation order does not change.
- **Splice hazard:** a line-range cut can overlap a *prior* extraction's
  re-import stub. After any extraction, grep the moved range for `^from \.`
  re-imports and confirm every leaf re-import still resolves.
- Behavior modules stay under 800 lines, enforced by
  `tools/check_module_size.py`. Modules already over the cap are enrolled in
  its `RATCHET` with an exact ceiling; the tool, not this list, is the
  inventory. `scenario_catalog.py` is the one *permanent* exception — a 2k-line
  ordered declarative registry that must not acquire validation or runtime
  orchestration. The rest are decomposition debt.
- Growing an enrolled module has two sanctioned remedies, and the choice turns
  on whether the addition is **separable**. Extract it when it is. Raise that
  module's ceiling in the same diff when it is not — a `typing` import, a
  widened annotation, one branch inside an existing function. The ratchet
  forbids *unreviewed* growth, not growth; a bump is one line someone reads.
  Do not decompose a 1k-line module to pay for an import.
- `tests/conftest.py::_load_amc` and the fresh-copy loaders in
  `test_correctness.py` / `test_determinism.py` load `legacy` **with package
  context** (a real submodule import or a dotted spec name) so these re-import
  seams resolve. A package-less `spec_from_file_location` copy fails on them.

## Determinism contract

For a fixed `--seed` and configuration, output bytes are a load-bearing
contract with locked SHA-256 golden hashes across `tests/`.

- The RNG is one `np.random.RandomState(seed)` created in `main()` and carried
  on `RunContext.rng`, passed explicitly through `generate_component()`,
  `_natural_column()`, and the anomaly override path. There is no module-level
  RNG and no module-level mutable anomaly state — do not reintroduce either.
- `generate_component()` sorts override specs with the stable key
  `(row_idx, metric_name)`. For specs at **distinct** `(row_idx, metric)`
  pairs, declaration order does not affect draws. When two specs **collide** on
  the same pair — two cascades rounding to one row at a coarse
  `--interval-seconds`, or a cascade inside a shaped primary span — the stable
  sort preserves input order and the **last writer wins**. Preserve declaration
  order within a scenario unless you have verified no collisions.
- `--anomaly-count` sampling depends on two orders: the `COMPONENTS` dict
  iteration order, and the order scenarios append into each component's list
  (the `SCENARIOS` insertion order). Preserve both unless you intend to shift
  the cap selection for the same seed.
- Determinism regressions to watch for in production code: a `set` iterated to
  build output-ordered rows (use `sorted()`), an *unseeded* `RandomState`
  fallback when `rng` is omitted, `id()`-based spec identity, and float
  `datetime.timestamp() * 1e9` where integer `timedelta` math is available.
- `generate_component()` is fully vectorized — one numpy op per metric column,
  anomalies as masked writes, CSV assembled via `np.char.add`. The suite drives
  full 1-day and 7-day runs end-to-end through `main()`; keep that path
  vectorized.

## Pipeline order

`generate_component()` runs one fixed sequence per component. Several rules
depend on this order, so changes here are behavior changes:

```
natural column draw → anomaly overrides → dtype="int" cast →
derivations → topology_capture snapshot → round → drop → CSV format
```

The `int` cast runs before derivations and before the capture, so derived
columns and downstream coupling signals see the same whole integers the CSV
records. Derived columns are recomputed *after* every override has settled, so
an anomaly targeting `cacheservice.hit_ratio` directly is silently overwritten
— drive `cache_hits` / `cache_misses` instead. Under realistic topology, each
downstream is generated after its upstreams in `_topology_generation_order`,
and cascade overrides are applied *after* saturation composition, so a cascade
still pins its own cell.

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
[testing-quality.md](.trellis/spec/amc/backend/testing-quality.md).

## Review readiness

The 15 pre-PR checklist headings and their per-heading bullets are canonical in
[testing-quality.md](.trellis/spec/amc/backend/testing-quality.md) § Review
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
[testing-quality.md](.trellis/spec/amc/backend/testing-quality.md); verify a
flag against current `HEAD` before acting, but treat flags as actionable by
default.

## Repository lints

Each guard carries its full contract — pattern, invocation modes, escape
hatches, and the `0` clean / `1` violation / `2` structural-error exit split —
in its own module docstring. Read the script, not a copy of it.

| Guard | Enforces |
| --- | --- |
| `tools/check_role_name_leaks.py` | internal role-name references in text-bearing files; stdin `-` mode pre-flights a comment body |
| `tools/check_approval_duplicate.py` | duplicate / self-correction `APPROVED` PR comments, keyed on (author, head commit) |
| `tools/pr_comment.sh` | the canonical wrapper: runs both comment gates, then `gh pr comment` |
| `tools/check_branch_name.py` | branch names republishing an internal ticket literal (`pre-push`; install with `pre-commit install --hook-type pre-push`) |
| `tools/check_ruff_lockstep.py` | the `ruff==` pin in `pyproject.toml` against the ruff-pre-commit `rev` |
| `tools/check_workflow_pip.py` | bare or unpinned `pip install` in workflows |
| `tools/check_test_resource_cost.py` | whole-file reads of generated CSVs under `tests/` |
| `tools/check_amc_module_load.py` | direct `spec_from_file_location` loads of `legacy.py` in tests |
| `tools/check_mypy_gate.py` | the canonical clean-module mypy gate command and list |
| `tools/check_module_size.py` | the 800-line behavior-module cap, ratcheted: an enrolled over-cap module grows only by a reviewed ceiling bump in the same diff, a finished extraction must drop its entry; `--list` prints the enrolled table |
| `tools/check_ci_review_contract.py` | CI cadence, action pins, partition commands, aggregate guards |
| `tools/check_copilot_instruction_contract.py` | checklist-heading lockstep across the spec, template, and Copilot instructions |
| `tools/check_task_criteria_commands.py` | quoted acceptance-criteria commands in `.trellis/tasks/**/*.md` that cannot produce the output they claim |
| `tools/check_guard_ci_coverage.py` | every `tools/check_*.py` on disk running in each of the three CI lanes (LIGHT / QUICK / FULL) its watched files can select, and each lint's own test file running in the QUICK lane; `--list` prints the per-lint coverage table |
| `tools/check_scope_heading_mirrors.py` | every prose description of the PR-body scope guard naming the category headings the guard actually recognizes, derived from `scripts/sd-ai-command-pack-pr-body-scope.py` merged with `.sd-ai-command-pack/pr-body-scope.json` rather than from a stored list; `--list` prints the mirror table |
| `tools/check_trellis_placeholders.py`, `tools/check_python_syntax.py`, `tools/check_agent_hook_exceptions.py`, `tools/check_trace_payload_antipatterns.py` | placeholder, syntax, hook-exception, and trace-payload shapes |

`tools/benchmark_combine.py` is the one intentional exception to the
every-tool-has-tests convention: a measurement harness, not a lint.

## Local gates

```bash
.venv/bin/pytest                       # parallel full suite
.venv/bin/pre-commit run --all-files   # lints, ruff, mechanical guards
.venv/bin/ruff check tests/
git diff --check
scripts/sd-ai-command-pack-full-check.sh   # the local review gate
```

Run the narrowest focused regression first, then affected suites, then broader
checks when the blast radius warrants it. CI is the merge gate — the required
branch-protection context is the aggregate `CI Result`; the local pre-commit
hooks do not run there. See
[testing-quality.md](.trellis/spec/amc/backend/testing-quality.md) for the lane
classification, the heavy/light partition, and the coverage and mypy gates.
