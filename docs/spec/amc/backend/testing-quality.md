# Testing and Quality

## Determinism and Single Sources of Truth

Preserve deterministic output for a fixed `--seed`. Avoid unordered set
iteration in output order, unseeded RNG fallbacks, `id()`-based identity, and
float timestamp arithmetic where integer `timedelta` math is available.
Sources: `src/anomaly_metric_creator/legacy.py`;
`tests/test_determinism.py`; `tests/test_correctness.py`;
`tests/test_schema_file.py`; `tests/test_gauges_file.py`.

Separate repeatability checks from absolute golden locks when choosing test
inputs. A test that asserts only "same arguments produce the same bytes" may
use the cheapest cadence that still exercises the behavior. An absolute hash
lock must document its exact cadence and emit selection; if those parameters
are deliberately coarsened, record maintainer approval, re-lock in an isolated
commit, and assert the retained semantic fields before the byte hash. For
`schema.json`, a standalone `--emit schema` run is valid when the consumer
reads no metric CSVs, but its changed `files` list is part of the new golden
contract. Sources: `tests/test_instances_per_component.py`;
`tests/test_schema_file.py`;
`.trellis/tasks/archive/2026-07/07-18-perf-heavy-fixture-trim/prd.md`.

Use canonical registries and helpers instead of parallel maps:
`COMPONENTS`, `SCENARIOS`, `DERIVATIONS`, `TOPOLOGY`,
`_EMIT_ARTIFACT_FILES`, `_COMBINE_OUTPUT_FILENAME`,
`_INSTANCE_DIMENSION_COLUMNS`, and related helpers. Sources:
`src/anomaly_metric_creator/legacy.py`;
`src/anomaly_metric_creator/scenario_catalog.py`;
`src/anomaly_metric_creator/scenarios_impl.py`; `tests/test_registry.py`;
`tests/test_emit_selection_hygiene.py`; `tests/test_validate_output.py`.

Dispatch tables should raise for unknown internal keys. Use strict indexing on
strict registries and let tolerant callers opt in locally with narrow
`try/except KeyError`. Sources:
`src/anomaly_metric_creator/legacy.py`; `tests/test_validate_output.py`;
`tests/test_scenarios.py`.

Scenario extraction keeps three independently reviewable behavior owners
(`scenario_builders.py`, `scenario_validation.py`, and `scenarios_impl.py`)
below 800 lines. `scenario_catalog.py` is the explicit *permanent* exception:
it may exceed that limit only as one declarative ordered registry, with no
validation or runtime orchestration. `tools/check_module_size.py` enforces the
cap and owns the enrolled list of over-cap modules; every other entry there is
decomposition debt on an exact ceiling. An enrolled module grows only by a
ceiling bump made in the same diff, so the increase is reviewed; extraction is
the remedy when the addition is separable, a bump when it is not.

Preserve facade/legacy object identity, patched
`legacy.SCENARIOS` visibility, and the single historical import-time validator
call. Sources: `src/anomaly_metric_creator/scenario_builders.py`;
`src/anomaly_metric_creator/scenario_catalog.py`;
`src/anomaly_metric_creator/scenario_validation.py`;
`src/anomaly_metric_creator/scenarios_impl.py`;
`src/anomaly_metric_creator/scenarios.py`;
`src/anomaly_metric_creator/legacy.py`; `tests/test_package_facades.py`;
`tests/test_registry.py`; `tests/test_scenarios.py`.

## Validation Strategy

Validate shape and type before membership tests, numeric operations, casts, or
iteration. Reject `None`, `NaN`, infinities, booleans as integers, negative
values, wrong containers, unhashables, empty strings, and wrong discriminator
branches where relevant. Sources:
`src/anomaly_metric_creator/legacy.py`;
`src/anomaly_metric_creator/server_traces.py`;
`src/anomaly_metric_creator/trace_bundle.py`; `tests/test_instance_config.py`;
`tests/test_scenarios.py`; `tests/test_validate_output.py`;
`tests/test_trace_bundle.py`.

Read-back files and payloads such as `schema.json`, `--instance-config`, serve
config files, command trace imports, and trace bundles must be validated on the
reader side even when the local writer normally creates them. Sources:
`README.md`; `src/anomaly_metric_creator/legacy.py`;
`src/anomaly_metric_creator/validate_impl.py`;
`src/anomaly_metric_creator/validate_cells.py`;
`src/anomaly_metric_creator/validate_topology.py`;
`src/anomaly_metric_creator/validate_topology_instances.py`;
`src/anomaly_metric_creator/server.py`;
`src/anomaly_metric_creator/server_traces.py`;
`src/anomaly_metric_creator/trace_bundle.py`; `tests/test_instance_config.py`;
`tests/test_server.py`; `tests/test_trace_bundle.py`.

## Test Layout

Tests live in `tests/`, write only to `tmp_path` or scoped temporary
directories, and should drive behavior through the installed/module surfaces
that users exercise. Sources: `README.md`; `tests/conftest.py`;
`tests/test_cli.py`; `tests/test_server.py`.

Use the session-scoped `amc` fixture and helpers in `tests/conftest.py`,
especially `_load_amc`, `run_capture`, `sha256_path`, and heavy-fixture
classification. Do not create duplicate module-scoped implementations of
expensive 1-day, 7-day, or N-instance datasets. Sources:
`README.md`; `tests/conftest.py`; `.pre-commit-config.yaml`;
`tests/test_run_capture_helper.py`; `tests/test_heavy_marker.py`.

Stream large generated files in tests. Use `sha256_path` or line iteration
instead of `Path.read_bytes()`, `readlines()`, or `read_text().splitlines()` on
multi-hundred-MB CSVs. Sources: `tests/conftest.py`;
`tests/test_correctness.py`; `tests/test_gauges_file.py`;
`tools/check_test_resource_cost.py`.

The `test-resource-cost` pre-commit hook and always-run CI repository guard
enforce those executable call shapes with Python AST parsing. A deliberately
small control, log, or schema read may use a trailing
`# resource-lint: allow` marker on the call's source span; prefer streaming for
generated data. Sources: `.pre-commit-config.yaml`; `.github/workflows/ci.yml`;
`tools/check_test_resource_cost.py`; `tests/test_test_resource_cost_lint.py`.

Tests that use POSIX-only modules or attributes must guard collection on
platforms where those APIs are missing. Sources: `tests/`;
`.github/workflows/ci.yml`.

A subprocess test asserting that `serve` **rejects** a flag combination must
pass a `timeout`. The assertion is that the process exits nonzero, so if the
gate ever regresses the invocation does not fail the test — it starts a real
blocking server and hangs the suite until the CI job's own limit kills it,
turning a specific gate regression into an unattributable timeout. A regression
must fail loudly, not hang. Sources: `tests/test_cli.py`
(`_SERVE_REJECT_TIMEOUT_SECONDS`).

## Pytest, Ruff, and Pre-Commit

The default pytest invocation uses xdist loadfile distribution with four
workers: `addopts = "-ra --dist loadfile -n 4"`, and `required_plugins`
requires `pytest-xdist` so missing xdist fails clearly. This is the canonical
local full-suite command. A shared session fixture can be instantiated on
`min(consuming files, workers)` processes even under loadfile distribution;
four workers are the measured saturation point for the file-granular suite.
Sources:
`pyproject.toml`; `README.md`; `tests/conftest.py`;
`.github/workflows/ci.yml`.

Use `-n 0` for true in-process serial runs such as `pdb`; `-n 1` still spawns
an xdist worker subprocess. The heavy/light CI partition is a memory-isolation
strategy, not the normal local speed path: on the 2026-07-20 checkout, the
complete default run took 253.36s while the serial heavy partition alone took
345.01s. Sources: `README.md`;
`pyproject.toml`.

The `heavy` marker is auto-applied by `tests/conftest.py` based on fixture
closure or a parametrized string naming a registered heavy fixture, not
hand-written on tests. The latter covers indirect `request.getfixturevalue`
lookups, which do not enter `item.fixturenames`. Each registered heavy fixture
name must resolve to exactly one definition so collection cannot bind a light
shadow fixture to a heavy name. CI runs both partitions under two-worker xdist
with `--dist loadfile`; the selectors keep the GB-scale fixtures out of the
light worker pool while preserving file-owned fixture locality. Sources:
`tests/conftest.py`; `pyproject.toml`;
`.github/workflows/ci.yml`; `README.md`;
`tests/test_heavy_marker.py`.

## Scenario: CI test partition worker contract

### 1. Scope / Trigger

- Trigger: a change to the heavy/light marker boundary, either pytest worker
  count, xdist distribution mode, or the GitHub-hosted runner capacity premise.
  Sources: `.github/workflows/ci.yml`; `tests/conftest.py`;
  `.trellis/tasks/archive/2026-07/07-18-perf-ci-worker-counts/design.md`.

### 2. Signatures

- Heavy lane: `pytest -n 2 --dist loadfile -m heavy --cov=src/anomaly_metric_creator --cov-report=`.
- Light lane: `pytest -n 2 --dist loadfile -m "not heavy" --cov=src/anomaly_metric_creator --cov-report=`.
  Sources: `.github/workflows/ci.yml`; `tools/check_ci_review_contract.py`.

### 3. Contracts

- Keep the GB-scale fixture closure in its own heavy lane. Two workers are
  adopted because run `29798826800` measured 5,333,032 KiB peak system used
  memory and 80,632,056 KiB post-run free disk, clearing the pre-committed
  12,582,912 KiB / 2,097,152 KiB thresholds. Keep `--dist loadfile` so each
  file's GB-scale fixtures remain on one worker.
- Keep `--dist loadfile` in the light lane so a test file and its shared
  fixtures stay on one worker. Retain two workers because the measured
  four-worker CI trial saved only 12 seconds against a 364-second baseline,
  below its pre-committed 100-second adoption threshold.
- The heavy and light selectors must remain disjoint and their collected counts
  must sum to the full suite. Sources: `tests/conftest.py`;
  `tests/test_heavy_marker.py`; `.github/workflows/ci.yml`;
  `.trellis/tasks/archive/2026-07/07-18-perf-ci-worker-counts/prd.md`.

### 4. Validation & Error Matrix

| Condition | Required result |
| --- | --- |
| Heavy marker closure becomes empty | `pytest -m heavy` exits 5 and fails CI |
| Light command loses `-n 2` or `--dist loadfile` | CI review contract guard fails |
| Heavy + light collection differs from full collection | Treat as a partition defect and do not publish |
| Heavy worker count is raised without runner evidence | Do not publish; runner evidence and a pre-committed decision boundary are required |

Sources: `tools/check_ci_review_contract.py`;
`tests/test_ci_review_contract.py`; `tests/test_heavy_marker.py`;
`.trellis/tasks/archive/2026-07/07-20-perf-ci-heavy-worker-trial/prd.md`.

### 5. Good/Base/Bad Cases

- Good: both selectors use `-n 2 --dist loadfile`, the GB-scale fixture closure
  remains isolated in the heavy lane, and both selectors cover the collection.
- Base: a local debugger run overrides the defaults with `-n 0` without
  changing the CI contract.
- Bad: `--dist load` scatters one file across workers, or the heavy lane is
  parallelized solely because it passed on a higher-capacity developer host.

### 6. Tests Required

- `tests/test_ci_review_contract.py` pins the exact heavy and light workflow
  commands and mutation-tests the live workflow contract.
- `tests/test_heavy_marker.py` pins fixture-closure classification, indirect
  parametrized lookup classification, and heavy-name uniqueness.
- Before publishing a worker-count change, collect the heavy, light, and full
  suites and assert that the first two counts sum to the third. Sources:
  `tests/test_ci_review_contract.py`; `tests/test_heavy_marker.py`;
  `.trellis/tasks/archive/2026-07/07-18-perf-ci-worker-counts/implement.md`.

### 7. Wrong vs Correct

Wrong:

```bash
pytest -n 2 --dist load -m "not heavy"
```

Correct:

```bash
pytest -n 2 --dist loadfile -m "not heavy"
```

`loadfile` keeps file-scoped fixture work on one worker and prevents worker
fan-out from multiplying expensive fixture construction. Sources:
`pyproject.toml`; `tests/conftest.py`; `.github/workflows/ci.yml`.

When two entry points produce byte-identical GB-scale artifacts through one
shared writer, co-locate their file-owned fixtures so `loadfile` creates each
artifact exactly once. Cache each output's streaming `sha256_path` digest,
retain an independent absolute hash guard per entry point, and compare the
runtime digests directly; shared structural assertions then need only scan one
of the byte-identical outputs. Sources: `tests/test_gauges_file.py`.

Ruff F401 is selected in `pyproject.toml` and scoped to tests by
`.pre-commit-config.yaml`; run `.venv/bin/ruff check tests/` or
`.venv/bin/pre-commit run --all-files` for touched test hygiene. Sources:
`pyproject.toml`; `.pre-commit-config.yaml`; `README.md`;
`.github/workflows/ci.yml`.

Additional mechanical guards catch recent review-churn patterns before PR
review: syntax-only `ast.parse` over Python files, Ruff F841 unused locals for
runtime/tools/hooks, agent-hook exception-shape checks, Trellis placeholder
and journal/index commit-list consistency checks, Copilot instruction contract
checks, trace-payload validation anti-pattern checks, and the canonical
clean-module mypy gate in `tools/check_mypy_gate.py`. CI invokes the
AMC-module-load, role-name, and agent-hook-exception guards from the always-run
changes job under uv-managed Python 3.14; role-name live-tree coverage includes
`src/`, `scripts/`, `.agents/`, and `.trellis/`. Keep these hooks
stdlib-only where they are local scripts, with the documented `0`/`1`/`2` exit
contract and acceptance tests over both temporary fixtures and the live repo
tree. `tools/benchmark_combine.py` is the one intentional exception to the
every-tool-has-tests convention: it is a measurement harness (imports the
project + numpy, not a `check_*` lint) with no `0`/`1`/`2` contract and no
acceptance test, and is not wired into pre-commit or CI. Sources:
`.pre-commit-config.yaml`;
`tools/check_python_syntax.py`;
`tools/check_agent_hook_exceptions.py`; `tools/check_trellis_placeholders.py`;
`tools/check_copilot_instruction_contract.py`;
`tools/check_trace_payload_antipatterns.py`;
`tools/check_mypy_gate.py`;
`tests/test_python_syntax_lint.py`;
`tests/test_agent_hook_exception_lint.py`;
`tests/test_trellis_placeholder_lint.py`;
`tests/test_copilot_instruction_contract.py`;
`tests/test_trace_payload_antipatterns_lint.py`;
`tests/test_mypy_gate_lint.py`.

Ruff is pinned in two places that must stay in lockstep: the `ruff==` dev-extra
pin in `pyproject.toml` and the `astral-sh/ruff-pre-commit` `rev` in
`.pre-commit-config.yaml`. Sources: `pyproject.toml`;
`.pre-commit-config.yaml`; `.github/workflows/ci.yml`;
`tests/test_ruff_lockstep_lint.py`.

Two other exact `==` pins have no automated update path — Dependabot's
`lockfile-only` `uv` strategy cannot move a manifest `==`, and the workflow
`python -m pip install` step is not a tracked ecosystem: `mypy==` in
`pyproject.toml`'s `dev`
extra and `socketsecurity==` in `.github/workflows/ci.yml`. Their manual bump
procedure (raise the pin, then verify the mypy baseline count is unchanged and
the gated clean-module list still passes / the Socket job stays green) lives in
the "Pinned CI tool bumps" subsection of `docs/DEVELOPMENT_CYCLE.md`, pointed at
from the pre-PR CI-hygiene heading. Sources: `pyproject.toml`;
`.github/workflows/ci.yml`; `docs/DEVELOPMENT_CYCLE.md`.

## Local and Remote Review Gates

Use `~/.agents/bin/sd-ai-command-pack-full-check.sh` as the local review gate rather than
manually assembling the recurring lint/test list. The pack-provided script runs
deterministic whitespace checks, the shared review preflight through
`~/.agents/bin/sd-ai-command-pack-review-preflight.mjs`, AMC's repo-local review
preflight through `scripts/check-review-preflight.mjs`, copied/generated scope
checks through `~/.agents/bin/sd-ai-command-pack-review-scope.sh`, the structural
install audit through `~/.agents/bin/sd-ai-command-pack-install-audit.py`,
current-diff CI classification, configured package scripts when present, and
optional Prism/Gito review. AMC's repo-local review preflight runs the CI/review
cadence contract guard, the Copilot instruction contract guard, the PR-body
scope guard, and the canonical clean-module mypy gate. Review-churn mutation
tests run in GitHub CI instead of being repeated by the repo-local preflight. Use
`SD_AI_COMMAND_PACK_FULL_CHECK_PRISM=0` or
`SD_AI_COMMAND_PACK_FULL_CHECK_GITO=0` to skip optional AI review while
iterating after the focused deterministic checks pass; re-enable Prism for the
final local review when practical. If the generated Obsidian KB freshness check
fails after a pull, refresh the gitignored output with
`.venv/bin/python3 ~/.agents/bin/sd-ai-command-pack-update-spec-kb.py` before
rerunning the gate. Use `SD_AI_COMMAND_PACK_FULL_CHECK_PRISM_FAIL_ON`,
`SD_AI_COMMAND_PACK_FULL_CHECK_PRISM_MAX_FINDINGS`, or
`SD_AI_COMMAND_PACK_FULL_CHECK_PRISM_RULES` to steer Prism without editing the script.
Sources: `~/.agents/bin/sd-ai-command-pack-full-check.sh`;
`~/.agents/bin/sd-ai-command-pack-review-preflight.mjs`;
`scripts/check-review-preflight.mjs`;
`~/.agents/bin/sd-ai-command-pack-review-scope.sh`;
`~/.agents/bin/sd-ai-command-pack-install-audit.py`;
`tools/check_ci_review_contract.py`;
`tools/check_copilot_instruction_contract.py`;
`~/.agents/bin/sd-ai-command-pack-pr-body-scope.py`;
`.sd-ai-command-pack/pr-body-scope.json`;
`tests/test_ci_change_classifier.py`;
`tests/test_ci_review_contract.py`;
`tests/test_copilot_instruction_contract.py`;
`tests/test_python_syntax_lint.py`;
`tests/test_workflow_pip_lint.py`; `tests/test_trellis_placeholder_lint.py`;
`tests/test_trace_payload_antipatterns_lint.py`; `tests/test_server.py`;
`docs/DEVELOPMENT_CYCLE.md`.

GitHub CI must keep the stable aggregate branch-protection context named
`CI Result`, while `scripts/classify-ci-changes.sh` selects the cheapest safe
application lane:
lightweight readiness for docs/spec/agent/review-tooling-only changes and
explicitly enumerated repo-only automation, quick
test for ordinary PR update churn that still touches app paths, and the full
Python 3.14 test lane for app-required opened/reopened/ready PRs,
`full-ci` label runs, auto-merge-armed PRs (the `auto_merge_enabled` event
and every later push or label event on an armed PR, via the payload's
`auto_merge` field),
workflow/dependency changes, manual dispatch, and `main` pushes. The full lane
uses concurrent `test_heavy` and `test_light` jobs followed by
`coverage_combine`; the light job owns the existing console-script, ruff, and
mypy gates plus the checksum-pinned kubectl v1.36.2 / Helm v4.2.0 real-client
server smokes, while the combine job merges visible raw-coverage artifacts,
generates XML, and enforces the 85% threshold. Auto-merge
must never merge on quick-lane evidence, and `main` pushes run in per-commit
concurrency groups so merge-burst runs cannot cancel each other's backstop
verdicts. The version policy (decided 2026-07-06) is
latest-stable-CPython-only: the single CI matrix version and
`requires-python` in `pyproject.toml` are the same value (currently
`>=3.14`) and move forward together when a new stable CPython lands —
there is no older declared floor and no multi-version lane. Sources:
`.github/workflows/ci.yml`; `scripts/classify-ci-changes.sh`;
`tools/check_ci_review_contract.py`; `tests/test_ci_change_classifier.py`;
`tests/test_ci_review_contract.py`; `docs/DEVELOPMENT_CYCLE.md`.

The `full-ci` label's lifetime is deliberately **asymmetric** between the two
workflows, and unifying them is a regression in either direction. The
application and Socket jobs in `ci.yml` honor it **one-shot** — only at the
`labeled` event, so a later plain `synchronize` drops the cost-gated full
matrix/scan back to the quick lane unless auto-merge is armed or
dependency/workflow files changed. `codeql.yml` honors it **persistently**: its
`synchronize` arm re-checks the label set
(`contains(github.event.pull_request.labels.*.name, 'full-ci')`) on every push,
so security analysis runs for the life of a flagged PR. Do not make CodeQL
one-shot; that cuts security coverage. `tools/check_ci_review_contract.py` pins
both semantics — a positive anchor on codeql's persistent re-check and a
`_require_not_contains` guard keeping that form out of `ci.yml`. Sources:
`.github/workflows/ci.yml`; `.github/workflows/codeql.yml`;
`tools/check_ci_review_contract.py`; `tests/test_ci_review_contract.py`.

The aggregate `test` job is guarded with `if: ${{ !cancelled() }}`, never
`always()`. When arming auto-merge triggers a fresh full run that cancels the
in-progress lane, the aggregate is cancelled *with* the run: its `test` context
reports `cancelled`, the required `CI Result` aggregate cannot pass, and
auto-merge waits for the superseding run's real verdict. `always()` would
instead run the aggregate during cancellation and evaluate
`test "cancelled" = "success"` — a transient red on every auto-merge-armed PR.
The contract guard pins the `!cancelled()` form so a revert is caught. A
superseded `main`-push commit's backstop run is deliberately *not* cancelled
(per-commit concurrency groups), so a merge burst spends N runner suites; that
is the accepted cost of the "every merge commit gets a completed verdict"
guarantee, not a bug. Sources: `.github/workflows/ci.yml`;
`tools/check_ci_review_contract.py`; `tests/test_ci_review_contract.py`.

On the *pull-request* side that same cancellation is not free, so **order the
lifecycle events to leave exactly one run in flight at the end**: apply
`full-ci` and take the PR out of draft *before* pushing the finish-work
bookkeeping commits, not after. Each of `ready_for_review`, `labeled`, and a
push starts a run whose concurrency group cancels the in-flight one, and the
cancelled run's rows stay attached to the head in `statusCheckRollup` beside
their replacements. GitHub's own branch protection resolves this correctly — it
evaluates the latest result per context name — but a merge-eligibility probe
that classifies each rollup row independently counts every `CANCELLED` row as
blocking and refuses a PR that GitHub reports `CLEAN` / `MERGEABLE`. On PR #360
that cost a full watch budget plus a `gh run rerun` of the superseded run purely
to stop its rows being cancelled; no check had failed. The rows are the
observable symptom of a *correct* cancellation, so nothing in this repository's
workflows needs changing — only the event order does. Sources:
`.github/workflows/ci.yml`; `docs/DEVELOPMENT_CYCLE.md`.

All events run on the standard `ubuntu-latest` runner. The org's
`ubuntu-latest-m` larger runner stopped being served on 2026-07-04 — main-push
jobs sat queued for hours with `runner_id=0`, so the post-merge backstop never
ran — and larger runners bill per-minute besides. Public-repository standard
runners provide 4 vCPU, 16 GB RAM, and 14 GB SSD with free minutes, so wall
clock rather than billed minutes is the optimization target; that 16 GB ceiling
is why the suite is split by the `heavy` marker instead of run in one worker
pool (a prior full `-n 2` run OOM-died after 32 minutes holding the N=3 / 7-day
fixtures across workers). Sources: `.github/workflows/ci.yml`;
`tests/conftest.py`; `docs/DEVELOPMENT_CYCLE.md`.

Coverage and mypy each run as a **report-only + gated** pair in the full lane.
mypy: a report-only baseline step (`continue-on-error: true`) over the whole
`[tool.mypy]` `files` set — `legacy.py` and the server layer are the known-messy
~137-error baseline — plus a gating step running
`mypy --follow-imports=silent` over the currently-clean modules, failing on any
error there. The command and gated list are owned by `tools/check_mypy_gate.py`,
which CI and the local review preflight both invoke; `--follow-imports=silent`
checks imports for inference but reports only errors originating in the listed
files, so importing still-dirty `legacy.py` does not leak into the gate. Grow
that list as decomposition extracts clean modules; never drop one to silence a
regression. `tests/test_mypy_gate_lint.py` asserts the list's length exactly, so
adding a module means updating that count in the same diff — the test is the
lockstep, not a doc rule.

**Never bind a builtin's name in a class body.** A method named `list`, `dict`,
`type`, or `id` is harmless at runtime — method bodies resolve names by LEGB and
class-body bindings are not in method scope — but mypy resolves *annotations* in
class scope, where the name is the method object. Every bare `list[...]`
annotation in that class then fails with `valid-type`, reported at the
annotation site and not at the method that caused it. This kept
`server_traces.py` out of the gate with 10 errors up to 700 lines from their
cause (`08-06-server-traces-mypy-gate`). Rename the method; an alias
re-creates the binding. Coverage: each pytest job runs `--cov=src/anomaly_metric_creator`
with no inline report, renames its hidden `.coverage` to a visible lane-specific
artifact, and uploads it; the coverage job combines both, generates
`coverage.xml`, and only then gates with `coverage report --fail-under=85` — a
no-regression ratchet ~3 points below the measured 88% for xdist/partition
jitter headroom, ratcheted **up** as decomposition lands and never lowered to
pass a red build. XML generation precedes the threshold step and `coverage.xml`
uploads with `if: ${{ !cancelled() }}` so it publishes even when the gate trips.
`[tool.coverage.run] relative_files = true` makes raw data portable across job
checkouts, and `COVERAGE_CORE=sysmon` keeps tracing overhead inside the job
timeout. The `--cov` flags stay CI-only — `addopts` / `required_plugins`
deliberately do not reference pytest-cov, so local runs pay no tracing cost.
Sources: `.github/workflows/ci.yml`; `tools/check_mypy_gate.py`;
`pyproject.toml`; `tests/test_mypy_gate_lint.py`.

## Scenario: GitHub Actions dependency pin updates

### 1. Scope / Trigger

- Trigger: any update to `actions/checkout`, `astral-sh/setup-uv`, or either
  `github/codeql-action` step in the repository workflows.
- This is an infrastructure contract spanning workflow execution, Dependabot
  grouping, cache behavior, and the local CI review guard.

### 2. Signatures

- Workflow action reference: `uses: <owner>/<action>@<40-lowercase-hex-SHA>`.
- Uniform-pin guard: `_single_pinned_action_revision(text, action, *, path,
  violations) -> str | None`.
- CodeQL Dependabot group: `patterns: ["github/codeql-action/*"]`.

### 3. Contracts

- Every `actions/checkout` use in `.github/workflows/ci.yml` must share one
  full commit SHA. Every `astral-sh/setup-uv` use in that workflow must also
  share one full commit SHA. The guard derives the accepted revision from the
  workflow instead of hard-coding today's dependency version.
- The coverage-combine job must use those same derived checkout and setup-uv
  revisions. A partial update is invalid even when each individual reference
  is pinned.
- CodeQL `init` and `analyze` must share one full commit SHA, and Dependabot
  must group `github/codeql-action/*` so its generated update is mergeable as
  one unit.
- setup-uv v9 changes the `prune-cache` default to `false`. Every CI step that
  sets `enable-cache: true` must also set `prune-cache: true` to preserve the
  repository's prior cache-size behavior explicitly.

### 4. Validation & Error Matrix

| Condition | Required result |
| --- | --- |
| Action uses a tag or short SHA | CI review contract fails with the offending revision |
| Same action has two full SHAs in `ci.yml` | CI review contract fails with both revisions |
| Coverage combine uses a different checkout or setup-uv SHA | CI review contract fails before remote CI |
| CodeQL init/analyze differ | CI review contract fails; do not merge either half |
| setup-uv cache enabled without explicit pruning | Treat as an unreviewed cache-cost behavior change |

### 5. Good/Base/Bad Cases

- Good: all checkout uses move to one new full SHA, all setup-uv uses move to
  one new full SHA, cached setup-uv steps retain `prune-cache: true`, and both
  CodeQL actions move together.
- Base: an unrelated workflow edit leaves all existing action revisions
  unchanged and uniform.
- Bad: one Dependabot PR updates CodeQL `init` while `analyze` remains on the
  previous SHA, or the contract checker is edited to bless a specific current
  dependency SHA.

### 6. Tests Required

- `tests/test_ci_review_contract.py` must prove a complete action SHA advance
  passes, mixed revisions fail, non-SHA references fail, and CodeQL
  init/analyze drift fails.
- `test_real_repo_contract_is_clean` must run against the edited live tree.
- Workflow dependency changes require the repository full gate and the remote
  full CI lane before merge.

### 7. Wrong vs Correct

Wrong:

```yaml
- uses: astral-sh/setup-uv@v9
- uses: actions/checkout@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
- uses: actions/checkout@bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
```

Correct:

```yaml
- uses: astral-sh/setup-uv@c771a70e6277c0a99b617c7a806ffedaca235ff9
  with:
    enable-cache: true
    prune-cache: true
- uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1
```

The correct form preserves supply-chain pinning and behavior while allowing a
complete future dependency update to advance without editing the guard's
source. Sources: `.github/workflows/ci.yml`;
`.github/workflows/codeql.yml`; `.github/dependabot.yml`;
`tools/check_ci_review_contract.py`; `tests/test_ci_review_contract.py`;
`https://github.com/astral-sh/setup-uv/releases/tag/v9.0.0`.

## Scenario: CI event and lightweight guard contract

### 1. Scope / Trigger

- Trigger: any change to CI event cadence, path classification, the
  lightweight guard runtime, or local/remote syntax-gate coverage.
- This is an infrastructure contract spanning GitHub event inputs, a shell
  classifier, workflow outputs, local pre-commit hooks, tests, and docs.

### 2. Signatures

- Classifier: `bash scripts/classify-ci-changes.sh [--force-app]
  [--github-output] changed-files.txt`.
- Lightweight Python guard: `uv run --python 3.14 --no-project python
  <stdlib-only-check> [paths...]`.
- Lightweight cache boundary: run
  `install -d -m 0700 -- "$UV_CACHE_DIR"` after `setup-uv` and before any
  pack-backed Python guard.
- Shell syntax gate: `bash -n <review-tooling-shell-scripts...>`.

### 3. Contracts

- `workflow_dispatch` appends `--force-app`, making `app_required=true` even
  for a documentation-only tip.
- `labeled` selects full CI when the applied label is `full-ci` or the event's
  pull request already has auto-merge armed.
- `.sd-ai-command-pack/**`, `.trellis/audit/**`, and the command-pack shell
  entrypoints are lightweight review/documentation surfaces. Dependency and
  workflow paths override that classification and force the full lane.
- `is_repo_tooling_path` may classify an explicit script lightweight only when
  doing so skips no behavioral test. Tested scripts and all `tools/` paths stay
  application-required; under-classification is safer than silently dropping
  coverage.
- Python syntax coverage includes top-level `scripts/*.py`. Both the workflow
  and pre-commit shell gates cover `sd-ai-command-pack-toolchain.sh` and
  `sd-ai-command-pack-shell-lib.sh`.
- `setup-uv` may expose a cache directory with group/other permissions. The
  lightweight lane must make that inherited override private before a guard
  imports `sd_ai_command_pack_lib`; the library's fail-closed cache boundary
  remains unchanged.

### 4. Validation & Error Matrix

| Condition | Required result |
| --- | --- |
| Manual dispatch of a docs-only tip | `app_required=true`; full lane eligible |
| Any later label event on an armed PR | `full_ci_requested=true` |
| Pack metadata or Trellis audit artifact only | lightweight lane |
| Dependency or workflow path mixed into that diff | full application lane |
| Python guard cannot run under managed 3.14 | lightweight job fails |
| Inherited `UV_CACHE_DIR` permits group/other access | harden it to `0700` before pack-backed guards; never relax the library check |
| Toolchain/shared-library shell syntax is invalid | local and remote syntax gates fail |

### 5. Good/Base/Bad Cases

- Good: `.sd-ai-command-pack/manifest.json` plus
  `.trellis/audit/ledger.md` stays lightweight, the uv cache is private, and
  the lane reports review tooling.
- Base: an ordinary runtime Python diff remains application-required.
- Bad: a docs-only manual dispatch remains lightweight, or a non-`full-ci`
  label on an armed PR rebuilds the required context from the quick lane, or
  pack-backed guards inherit a group/other-accessible uv cache.

### 6. Tests Required

- `tests/test_ci_change_classifier.py` asserts pack/audit positive cases and
  runtime/dependency/workflow negative cases.
- `tests/test_ci_review_contract.py` mutation-tests the labeled auto-merge
  clause, manual-dispatch force-app, every managed-Python lightweight guard
  command, private-cache setup ordering, and both syntax lists against the live
  repository.
- `tests/test_python_syntax_lint.py` parses all tracked Python under
  `scripts/`, `src/`, `tests/`, `tools/`, and generated hook roots.

### 7. Wrong vs Correct

Wrong:

```bash
# setup-uv cache permissions are inherited unchanged.
uv run --python 3.14 --no-project python tools/check_copilot_instruction_contract.py

# Manual dispatch can leave a docs-only diff app_required=false.
bash scripts/classify-ci-changes.sh --github-output changed-files.txt

# Any non-full-ci label ignores the armed PR state.
if [ "$PR_LABEL" = "full-ci" ]; then
  full_ci_requested=true
fi
```

Correct:

```bash
install -d -m 0700 -- "$UV_CACHE_DIR"
uv run --python 3.14 --no-project python tools/check_copilot_instruction_contract.py

classifier_args=(--github-output)
if [ "$EVENT_NAME" = "workflow_dispatch" ]; then
  classifier_args+=(--force-app)
fi

if [ "$PR_LABEL" = "full-ci" ] || [ "$PR_AUTO_MERGE" = "true" ]; then
  full_ci_requested=true
fi
```

Sources: `.github/workflows/ci.yml`; `.pre-commit-config.yaml`;
`scripts/classify-ci-changes.sh`; `tests/test_ci_change_classifier.py`;
`tools/check_ci_review_contract.py`;
`tests/test_ci_review_contract.py`;
`tests/test_python_syntax_lint.py`; `docs/DEVELOPMENT_CYCLE.md`.

CodeQL analyzes opened/reopened/ready_for_review PRs and `full-ci`-labeled
updates; plain `synchronize` events keep the trigger but report a skipped
analysis job, and merged code is always analyzed by the push-to-main run.
CodeQL is advisory on PRs: branch protection requires only the `CI Result`
context, which aggregates the application and Socket jobs (a skipped analysis
produces no code-scanning summary
check, so `CodeQL` must not be a required context while this gating is in
place). Keep the `synchronize` trigger itself: once `full-ci` is applied,
later pushes to the PR re-analyze automatically.
Pin the CodeQL `init` and `analyze` steps to the same exact 40-character action
revision. A partial dependency update can otherwise initialize one action
version and analyze with another, failing before queries run; the CI contract
guard and its mutation test must reject that drift locally.
Socket should keep a visible PR check but fast-skip unless
dependency/security-relevant files changed or full CI was requested. Sources:
`.github/workflows/codeql.yml`; `.github/workflows/ci.yml`;
`scripts/classify-ci-changes.sh`; `tools/check_ci_review_contract.py`;
`docs/DEVELOPMENT_CYCLE.md`.

Dependabot auto-merge should enable GitHub auto-merge for patch/minor updates
without trying to approve the pull request with `GITHUB_TOKEN`, because this
repo's workflow token is not allowed to create PR reviews. Sources:
`.github/workflows/dependabot-auto-merge.yml`;
`tools/check_ci_review_contract.py`; `tests/test_ci_review_contract.py`.

No workflow in this repository refreshes the command pack. The thin conversion
moved the payload to the machine install, leaving nothing here for a scheduled
installer run to change, so the contract this section used to state — fixed PR
branch, no-diff suppression, scoped-token writes — has no workflow to bind and
is not replaced by an equivalent. A CI lane that refreshes the pack is a
regression, not a gap: refreshes are operator-initiated against the machine
install. Sources: `.github/workflows/`, which contains no pack-refresh
workflow; `tools/check_ci_review_contract.py`, whose `REQUIRED_FILES` no longer
names one.

Windows portability coverage is collection-only and advisory: pull requests
sync the locked Python 3.14 development environment on `windows-latest`, then
run `pytest --collect-only -q`. The job must use `continue-on-error: true` and
must not appear in the `test` or `CI Result` dependency lists. Sources:
`.github/workflows/ci.yml`; `tools/check_ci_review_contract.py`;
`tests/test_ci_review_contract.py`;
`docs/DEVELOPMENT_CYCLE.md`.

## Review Checklist

Before marking a PR ready, walk these headings: scope and description,
validators/schema, docs/docstrings, single source of truth, completeness,
mode/flag combinations, deterministic test paths, hot-path performance,
user-facing output order, test hygiene, test resource cost, cross-platform
guards, default-behavior changes, CI/workflow/dependency hygiene, and
changelog/version impact. Sources:
`.github/PULL_REQUEST_TEMPLATE.md`;
`.github/instructions/anomaly-metric-creator.instructions.md`.

These 15 headings map to the recurring review gates identified by the full
sweep of ~750 Copilot comments through PR #122 plus the 0.4.0 release-hygiene
audit. Confirm each heading before removing draft status, or write "N/A —
_reason_". The bullets under each heading are guidance for what to verify, not
extra checklist entries to copy verbatim. `.github/PULL_REQUEST_TEMPLATE.md`
prefills the same 15 headings as Markdown `- [ ]` lines and must mirror — not
redefine — them; when a heading is renamed, added, or removed here, update the
template, the Copilot instructions, and
`tools/check_copilot_instruction_contract.py` in the same diff.

Doc/comment-vs-code drift is the single most-flagged pattern (~30% of all
review comments), so **Doc / docstring sync** is the highest-leverage heading to
actually run rather than skim.

### Scope & description

- The PR description names every behavior change in the diff — RNG model,
  registries, module-level state, default-output bytes, public-helper
  signatures, CLI/env semantics, doc surface. If the diff is broader than the
  description, split the PR or update the description.
- A diff touching RNG, `RunContext`, registries, or module-level state calls
  that out explicitly, and the test plan covers determinism.

### Validators and schema checks

- For every field a new validator inspects, enumerate non-canonical inputs:
  `None`, `NaN`, `±inf`, negative, `bool` (a subtype of `int`), empty string,
  unhashable, wrong container type.
- Type-check *before* a membership test or numeric op, so the validator's own
  `ValueError` fires instead of a raw exception from deeper in: `x in VALID_SET`
  raises `TypeError` on an unhashable list/dict — gate with `isinstance(x, str)`
  first; `math.isfinite(x)` raises `OverflowError` on an arbitrarily large `int`
  at import time — guard or skip the float path for non-float numerics.
- `schema.json` (and any `--instance-config` or other hand-editable input read
  back at runtime) is **untrusted**: every field the *reader* consumes needs the
  same type + finiteness guards as the writer-side check. A `NaN`/`±inf` that a
  JSON loader happily parses silently defeats range and zero-variance checks
  downstream (`np.std` returns `NaN`; every comparison against it is `False`).
- Every *branch* of a discriminator is validated: callable **and** constant
  `Edge.weight`; cascade **and** primary specs; step **and** span paths; `*args`
  **and** fixed-arity callables.
- Dispatch tables (`_RECOMPUTERS`, `DERIVATIONS`, …) raise on unknown keys and
  never return `None` or fall through silently. Antipatterns to grep for:
  `table.get(key)` on a dispatch table (use `table[key]` so registry drift fails
  loudly); a dispatcher function returning a sentinel or "soft violation" for an
  unrecognized metric/component instead of raising `KeyError` (the caller cannot
  distinguish "fine" from "no recomputer"); a dispatcher branch falling through
  to a bare `return` when no `if`/`elif` matched.

### Doc / docstring sync

- Every changed function with a docstring has its docstring updated in the same
  diff.
- Grep every changed symbol name against the Trellis specs, `README.md`, and
  `docs/`, and update prose that describes it.
- When you change a default, precedence rule, count, edge list, or dispatch
  order, grep for the *old value/word* across the docstring, in-file section
  headers, CLI `--help` strings, `README.md`, `docs/*.md`, and the specs. A
  behavior change fans out across all of them (flipping `--topology-mode` to
  `realistic` left `docs/topology.md` stale; moving subcommand dispatch before
  `parse_args` left the `docs/application-flow.md` mermaid wrong).
- Magnitude/percentage values baked into description strings (a scenario's
  `(35% errors)`, a docstring's `350 rows`) must match the generator.
- Count words drift silently as a list grows — "four slices", "three modes",
  "8 specs". Re-count after adding or removing an item.
- A new `tools/check_*.py` whose docstring was copy-pasted from a sibling must
  have its mode/call counts and examples re-verified line-by-line (PR #92
  inherited "three modes / three calls" while having two modes and four `gh`
  calls).
- After any bulk find/replace, re-read every touched docstring for orphaned
  grammar — each fragment is filed as its own review comment (PR #80).

### Single source of truth

- No hand-rolled emit→filename, metric→component, or component→derivation maps
  alongside a canonical registry.
- `_COMBINE_OUTPUT_FILENAME` is used by the actual combine writer, not only the
  cleanup/summary path.
- `Instance` dimension fields have multiple drift sites — `_valid_instance_fields`
  and the `Instance(**{...})` constructor kwargs in `_load_instance_config` —
  both derive from `_INSTANCE_DIMENSION_COLUMNS`, never a hand-listed copy
  (#64). Same for any "canonical first entry" *positional* convention (a
  `break`-after-first over `_TOPOLOGY_LOAD_METRICS`): make it explicit, not
  implicit in iteration order (#47).

### Completeness

- A PR title implying a class of fix ("add `clip_min` to non-negative metrics")
  means grepping for all instances and confirming coverage.
- When a change adds a *second* code path for the same data — wide vs long-form
  CSV, anonymous vs named-instance, 4-col vs 10-col gauges, lambda-baked vs
  per-instance topology — list every transform, guard, default, and splice the
  original path applies and confirm each is re-applied. Recurring misses:
  `_splice_dst_artifact` dropped on the long-form writer (#63); a
  `header[0] == "timestamp"` check missing from dim-detection (#67); an eagerly
  evaluated `config_map.get(name, list(INSTANCES[name]))` default that crashed
  the unconfigured branch (#64).

### Mode / flag combinations

- List every other CLI flag, env var, and `--emit` token that interacts with a
  new flag. Gate invalid combinations in `parse_args` with a clear message, or
  add a test.
- New `parse_args` checks must not spuriously reject the `combine`/`validate`
  subcommands or non-default `--emit` invocations.

### Test path determinism

- Every new code path has a test whose input deterministically exercises it (no
  reliance on "the default seed happens to do X"). Each new CLI flag is covered
  in isolation, not only in the most-permissive bundle.
- If `expected` is derived from a registry, assert it is non-empty *before* the
  membership/equality check. An empty `expected` passes vacuously in several
  shapes: `expected.issubset(actual)` (`∅ ⊆ actual`), `for m in expected:
  assert …` (zero iterations), `actual == expected` (both empty),
  `expected & actual == expected`, `actual.issuperset(expected)`. Three of four
  vacuous-test bugs on PR #50 had this shape. Where emptiness is legitimately
  possible, assert that *condition* explicitly and gate the membership check
  behind it.
- Pair every "negative" assertion (the dropped scenario's output is absent) with
  a positive one (a retained scenario's output survives), and for a dropped
  scenario assert its *cascade* specs are absent too (cascade leakage went
  undetected on #13/#16). A file-existence assertion must also read ≥1 data row.
- String matching that must be exact uses anchored regex or full-token equality,
  never bare `in`: version-pin parsing (a `ruff==0.15.17` regex must end-anchor
  or a `; python_version<…` marker slips through, #117), flag-presence tests
  (`"--emit" in out` false-positives once `--emit-selection` exists, #101/#104),
  trailing-marker escape hatches (`# allow` matched mid-line fires inside string
  literals, #89).
- Avoid tautological boolean assertions: `assert A or B` where `B` is
  unconditionally true always passes (#68). A "negative" test must also assert
  the run reached the intended code path.

### Performance in hot paths

- No per-row re-parsing of strings or re-computation of hoistable constants. A
  timestamp re-`strptime`d once per data point is a real hotspot at gauge-stream
  scale (#30).
- No broad `try/except` in a per-row loop whose body has side effects such as
  RNG draws. Resolve a generator's arity once per spec, not once per row (#37).
- Per-`(component, instance)` loops multiply cost by N: hoist per-component file
  scans above the instance loop, and do not re-open the same CSV from the start
  for each instance block (#67).

### Action order in user-facing output

- The end-of-run `Done - … written to …` summary names only artifacts the run
  actually wrote, and prints only after every writer it names has succeeded.

### Test hygiene

- No unused imports or helpers in new test files (ruff F401 via pre-commit).
- New test files reuse the session-scoped `amc` fixture and do not re-import
  `legacy.py` via `importlib.util.spec_from_file_location`. The
  `amc-no-direct-spec-load` hook (`tools/check_amc_module_load.py`) catches this
  structurally. When a fresh module instance is genuinely needed, route through
  `conftest._load_amc()` or annotate the line with `# amc-load: allow`.
- An in-process `main()` call must not leave mutated module/session state (a
  filtered cascade registry, `MEZMO_OTEL_*` env vars) visible to later tests —
  under parallel xdist that becomes an order-dependent flake. An autouse
  env-isolation fixture must out-scope the session fixtures it protects (#17).

### Test resource cost

- The AST-backed `tools/check_test_resource_cost.py` guard rejects executable
  `read_bytes()`, `readlines()`, and `read_text().splitlines()` under `tests/`;
  a trailing `# resource-lint: allow` is for reviewed, deliberately small
  artifacts only.
- Fixtures generating full 1-day, 7-day, or N>1-instance datasets reuse the
  session-scoped fixtures in `tests/conftest.py` rather than redefining
  module-scoped duplicates (PR #67 had three separate 264 MiB N=3 fixtures).
- Hash large files with the shared streaming `conftest.sha256_path`, and count
  rows with `sum(1 for _ in f)` rather than `readlines()`.

### Cross-platform test guards

- **POSIX-only modules** (`resource`, `pwd`, `grp`, `fcntl`, `termios`, `tty`):
  guard with `pytest.importorskip("resource")` inside the test body, or a
  module-top `pytest.skip(..., allow_module_level=True)` *before* the import. An
  unconditional top-of-module `import resource` fails collection on Windows.
- **POSIX-only names on cross-platform modules** (`select.epoll`,
  `signal.SIGSTOP`, `os.fork`): `importorskip` is the wrong guard because the
  module imports fine. Use a module-top platform skip before a `from … import`,
  or `pytest.skipif(not hasattr(select, "epoll"), …)` on the individual test.

### Default-behavior changes

- If a default parameter value or fallback path changes (unseeded
  `RandomState`, a required arg replacing an optional one), the PR description
  names it and tests cover both caller shapes.
- Production determinism regressions are as load-bearing as test ones: a `set`
  iterated to build output-ordered rows, an unseeded `RandomState` fallback, an
  `id()`-based identity, or float `datetime.timestamp()*1e9` all break the seed
  guarantee (#9/#19/#37).

### CI / workflow / dependency hygiene

- Pin third-party actions and in-workflow installs to exact versions; use
  `python -m pip`, never bare `pip`, after `actions/setup-python`.
- A job's `permissions:` grants exactly the scopes its steps need. Gate
  secret-bearing triggers on actor/permission, and remember a fork
  `pull_request` gets no secrets.
- Two-place version pins are lint-enforced in lockstep; a Dependabot bump must
  not silently raise a declared `>=` floor (`versioning-strategy:
  lockfile-only`), and the `package-ecosystem` must match a lockfile that
  exists. Exact `==` pins Dependabot cannot reach have a manual path in the
  "Pinned CI tool bumps" section of `docs/DEVELOPMENT_CYCLE.md`.
- Docs that tell users to run a tool ensure it is in the `dev` extra; `addopts`
  plugin flags require a matching `required_plugins`; workflow shell snippets
  must not assume runner-image tools without installing them.
- A new `tools/check_*.py` honors the `0`/`1`/`2` exit contract in its own
  docstring: a decode/IO failure exits `2`, not a traceback and not `1`; check
  `path.exists()` before skip-rules; parse `gh api --paginate` page-by-page;
  use anchored matching for markers and pins.

### Changelog / version impact

- User-visible behavior, compatibility changes, supported Python floors, and
  release-process changes update `CHANGELOG.md` in the same PR, or the PR states
  why no entry is warranted.
- A release PR keeps `pyproject.toml`, the editable project entry in `uv.lock`,
  the promoted changelog heading, tag name, GitHub Release, and `amc --version`
  aligned.

Sources: `.github/PULL_REQUEST_TEMPLATE.md`;
`.github/instructions/anomaly-metric-creator.instructions.md`;
`tools/check_copilot_instruction_contract.py`;
`tools/check_test_resource_cost.py`; `tools/check_amc_module_load.py`;
`docs/DEVELOPMENT_CYCLE.md`; `docs/REVIEW_PATTERNS.md`.

PRs open as **draft** and walk this checklist before draft status is removed, so
issues are caught before Copilot's first review rather than after. Sources:
`.github/PULL_REQUEST_TEMPLATE.md`; `docs/DEVELOPMENT_CYCLE.md`.

When a recurring issue is mechanical and greppable, prefer a `tools/check_*.py`
lint plus tests over prose-only rules — the `ruff-lockstep` / `role-name-leaks`
/ `branch-name` lints reliably stop their patterns, whereas prose rules have
not (the test-resource-cost rules recurred across several PRs after being
documented). Sources:
`.pre-commit-config.yaml`; `tests/test_role_name_leaks_lint.py`;
`tests/test_branch_name_lint.py`; `tests/test_ruff_lockstep_lint.py`;
`tests/test_workflow_pip_lint.py`.

### Known Copilot false positives (verify, don't reflexively fix)

The maintainer accepted ~98% of Copilot's flags across 122 PRs, so the default
is to treat a flag as actionable. The recurring exceptions, worth recognizing so
they do not cost a review cycle:

- **Cumulative-diff re-flagging.** Copilot reviews the PR's *cumulative* diff,
  so it re-flags an issue already fixed in a later commit of the same PR. Verify
  against current `HEAD` before "fixing" it again (#80).
- **Triplicated drift.** The same stale sentence flagged from three nearby hunks
  is one defect, not three — fix once (#14/#20/#27).
- **"`contents: read` breaks the setup-uv / Actions cache."** False. The cache
  authenticates with `ACTIONS_RUNTIME_TOKEN`, independent of the `GITHUB_TOKEN`
  `permissions:` block (#117).
- **"Secrets can be referenced in a step-level `if:`."** False at job *and* step
  level. Mapping the secret to `env` and gating on a derived step output is
  required, not a workaround to remove (#118).
- **"Skip the preflight cell-cap when `--emit` excludes `metrics`."** False —
  `generate_component()` still allocates the full array and runs the pipeline
  regardless of emit selection; only the final write is gated, so the OOM the
  cap prevents still happens. Only the `combine`/`validate` subcommands (which
  `return` before generation) are safe skips (#35).
- **"The ratchet ceiling is off by one — the file really has N+1 physical
  lines (it ends with a trailing blank line)."** False, and it recurs on every
  ceiling bump because a bump is the one diff that puts a raw line count in
  front of a reviewer. A file ending in a single `\n` terminator has no
  trailing blank line to count, so `wc -l` on the file at current `HEAD`
  settles it — not reasoning about newline semantics. `wc -l` is the right
  arbiter here because `tools/check_module_size.py` agrees with it on every
  newline-terminated file; the two diverge only for a file whose last line has
  *no* terminator, which the lint counts and `wc -l` does not. That divergence
  is deliberate and documented in the lint's own Counting section, and it moves
  the count in the opposite direction from the one the flag claims (#360).

For any version-sensitive claim about a tool's semantics (Actions, uv,
Dependabot, pytest), confirm against current docs before accepting — Copilot's
confident-but-wrong claims cluster there. Otherwise treat Copilot and AI review
comments as actionable by default, verifying against current `HEAD`, code
comments, and actual trust boundaries before fixing. Sources:
`.github/instructions/anomaly-metric-creator.instructions.md`;
`.github/workflows/ci.yml`; `docs/REVIEW_PATTERNS.md`.

## Verification Commands

Common local checks are:

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check tests/
.venv/bin/pre-commit run --all-files
git diff --check
```

Run the narrowest focused regression first, then affected files/suites, then
broader checks when blast radius warrants it. Sources: `README.md`;
`pyproject.toml`; `.pre-commit-config.yaml`; `.github/workflows/ci.yml`;
`tests/`.
