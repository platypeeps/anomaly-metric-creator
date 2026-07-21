# Testing and Quality

## Determinism and Single Sources of Truth

Preserve deterministic output for a fixed `--seed`. Avoid unordered set
iteration in output order, unseeded RNG fallbacks, `id()`-based identity, and
float timestamp arithmetic where integer `timedelta` math is available.
Sources: `CLAUDE.md`; `src/anomaly_metric_creator/legacy.py`;
`tests/test_determinism.py`; `tests/test_correctness.py`;
`tests/test_schema_file.py`; `tests/test_gauges_file.py`.

Use canonical registries and helpers instead of parallel maps:
`COMPONENTS`, `SCENARIOS`, `DERIVATIONS`, `TOPOLOGY`,
`_EMIT_ARTIFACT_FILES`, `_COMBINE_OUTPUT_FILENAME`,
`_INSTANCE_DIMENSION_COLUMNS`, and related helpers. Sources: `CLAUDE.md`;
`src/anomaly_metric_creator/legacy.py`; `tests/test_registry.py`;
`tests/test_emit_selection_hygiene.py`; `tests/test_validate_output.py`.

Dispatch tables should raise for unknown internal keys. Use strict indexing on
strict registries and let tolerant callers opt in locally with narrow
`try/except KeyError`. Sources: `CLAUDE.md`;
`src/anomaly_metric_creator/legacy.py`; `tests/test_validate_output.py`;
`tests/test_scenarios.py`.

## Validation Strategy

Validate shape and type before membership tests, numeric operations, casts, or
iteration. Reject `None`, `NaN`, infinities, booleans as integers, negative
values, wrong containers, unhashables, empty strings, and wrong discriminator
branches where relevant. Sources: `CLAUDE.md`;
`src/anomaly_metric_creator/legacy.py`;
`src/anomaly_metric_creator/server_traces.py`;
`src/anomaly_metric_creator/trace_bundle.py`; `tests/test_instance_config.py`;
`tests/test_scenarios.py`; `tests/test_validate_output.py`;
`tests/test_trace_bundle.py`.

Read-back files and payloads such as `schema.json`, `--instance-config`, serve
config files, command trace imports, and trace bundles must be validated on the
reader side even when the local writer normally creates them. Sources:
`README.md`; `CLAUDE.md`; `src/anomaly_metric_creator/legacy.py`;
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
that users exercise. Sources: `CLAUDE.md`; `README.md`; `tests/conftest.py`;
`tests/test_cli.py`; `tests/test_server.py`.

Use the session-scoped `amc` fixture and helpers in `tests/conftest.py`,
especially `_load_amc`, `run_capture`, `sha256_path`, and heavy-fixture
classification. Do not create duplicate module-scoped implementations of
expensive 1-day, 7-day, or N-instance datasets. Sources: `CLAUDE.md`;
`README.md`; `tests/conftest.py`; `.pre-commit-config.yaml`;
`tests/test_run_capture_helper.py`; `tests/test_heavy_marker.py`.

Stream large generated files in tests. Use `sha256_path` or line iteration
instead of `Path.read_bytes()`, `readlines()`, or `read_text().splitlines()` on
multi-hundred-MB CSVs. Sources: `CLAUDE.md`; `tests/conftest.py`;
`tests/test_correctness.py`; `tests/test_gauges_file.py`;
`tools/check_test_resource_cost.py`.

The `test-resource-cost` pre-commit hook and always-run CI repository guard
enforce those executable call shapes with Python AST parsing. A deliberately
small control, log, or schema read may use a trailing
`# resource-lint: allow` marker on the call's source span; prefer streaming for
generated data. Sources: `.pre-commit-config.yaml`; `.github/workflows/ci.yml`;
`tools/check_test_resource_cost.py`; `tests/test_test_resource_cost_lint.py`.

Tests that use POSIX-only modules or attributes must guard collection on
platforms where those APIs are missing. Sources: `CLAUDE.md`; `tests/`;
`.github/workflows/ci.yml`.

## Pytest, Ruff, and Pre-Commit

The default pytest invocation uses xdist loadfile distribution with four
workers: `addopts = "-ra --dist loadfile -n 4"`, and `required_plugins`
requires `pytest-xdist` so missing xdist fails clearly. This is the canonical
local full-suite command. A shared session fixture can be instantiated on
`min(consuming files, workers)` processes even under loadfile distribution;
four workers are the measured saturation point for the file-granular suite.
Sources:
`pyproject.toml`; `README.md`; `CLAUDE.md`; `tests/conftest.py`;
`.github/workflows/ci.yml`.

Use `-n 0` for true in-process serial runs such as `pdb`; `-n 1` still spawns
an xdist worker subprocess. The heavy/light CI partition is a memory-isolation
strategy, not the normal local speed path: on the 2026-07-20 checkout, the
complete default run took 253.36s while the serial heavy partition alone took
345.01s. Sources: `README.md`; `CLAUDE.md`;
`pyproject.toml`.

The `heavy` marker is auto-applied by `tests/conftest.py` based on fixture
closure, not hand-written on tests. CI runs both partitions under two-worker
xdist with `--dist loadfile`; the selectors keep the GB-scale fixtures out of
the light worker pool while preserving file-owned fixture locality. Sources:
`tests/conftest.py`; `pyproject.toml`;
`.github/workflows/ci.yml`; `CLAUDE.md`; `README.md`;
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
- `tests/test_heavy_marker.py` pins fixture-closure classification.
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

Ruff F401 is selected in `pyproject.toml` and scoped to tests by
`.pre-commit-config.yaml`; run `.venv/bin/ruff check tests/` or
`.venv/bin/pre-commit run --all-files` for touched test hygiene. Sources:
`pyproject.toml`; `.pre-commit-config.yaml`; `README.md`; `CLAUDE.md`;
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
`.pre-commit-config.yaml`; `CLAUDE.md`; `.github/workflows/ci.yml`;
`tests/test_ruff_lockstep_lint.py`.

## Local and Remote Review Gates

Use `scripts/sd-ai-command-pack-full-check.sh` as the local review gate rather than
manually assembling the recurring lint/test list. The pack-provided script runs
deterministic whitespace checks, the shared review preflight through
`scripts/sd-ai-command-pack-review-preflight.mjs`, AMC's repo-local review
preflight through `scripts/check-review-preflight.mjs`, copied/generated scope
checks through `scripts/sd-ai-command-pack-review-scope.sh`, the structural
install audit through `scripts/sd-ai-command-pack-install-audit.py`,
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
`.venv/bin/python3 scripts/sd-ai-command-pack-update-spec-kb.py` before
rerunning the gate. Use `SD_AI_COMMAND_PACK_FULL_CHECK_PRISM_FAIL_ON`,
`SD_AI_COMMAND_PACK_FULL_CHECK_PRISM_MAX_FINDINGS`, or
`SD_AI_COMMAND_PACK_FULL_CHECK_PRISM_RULES` to steer Prism without editing the script.
Sources: `scripts/sd-ai-command-pack-full-check.sh`;
`scripts/sd-ai-command-pack-review-preflight.mjs`;
`scripts/check-review-preflight.mjs`;
`scripts/sd-ai-command-pack-review-scope.sh`;
`scripts/sd-ai-command-pack-install-audit.py`;
`tools/check_ci_review_contract.py`;
`tools/check_copilot_instruction_contract.py`;
`scripts/sd-ai-command-pack-pr-body-scope.py`;
`.sd-ai-command-pack/pr-body-scope.json`;
`tests/test_ci_change_classifier.py`;
`tests/test_ci_review_contract.py`;
`tests/test_copilot_instruction_contract.py`;
`tests/test_pr_body_scope_lint.py`;
`tests/test_python_syntax_lint.py`;
`tests/test_workflow_pip_lint.py`; `tests/test_trellis_placeholder_lint.py`;
`tests/test_trace_payload_antipatterns_lint.py`; `tests/test_server.py`;
`docs/DEVELOPMENT_CYCLE.md`.

GitHub CI must keep the stable aggregate branch-protection context named
`CI Result`, while `scripts/classify-ci-changes.sh` selects the cheapest safe
application lane:
lightweight readiness for docs/spec/agent/review-tooling-only changes, quick
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
- Shell syntax gate: `bash -n <review-tooling-shell-scripts...>`.

### 3. Contracts

- `workflow_dispatch` appends `--force-app`, making `app_required=true` even
  for a documentation-only tip.
- `labeled` selects full CI when the applied label is `full-ci` or the event's
  pull request already has auto-merge armed.
- `.sd-ai-command-pack/**`, `.trellis/audit/**`, and the command-pack shell
  entrypoints are lightweight review/documentation surfaces. Dependency and
  workflow paths override that classification and force the full lane.
- Python syntax coverage includes top-level `scripts/*.py`. Both the workflow
  and pre-commit shell gates cover `sd-ai-command-pack-toolchain.sh` and
  `sd-ai-command-pack-shell-lib.sh`.

### 4. Validation & Error Matrix

| Condition | Required result |
| --- | --- |
| Manual dispatch of a docs-only tip | `app_required=true`; full lane eligible |
| Any later label event on an armed PR | `full_ci_requested=true` |
| Pack metadata or Trellis audit artifact only | lightweight lane |
| Dependency or workflow path mixed into that diff | full application lane |
| Python guard cannot run under managed 3.14 | lightweight job fails |
| Toolchain/shared-library shell syntax is invalid | local and remote syntax gates fail |

### 5. Good/Base/Bad Cases

- Good: `.sd-ai-command-pack/manifest.json` plus
  `.trellis/audit/ledger.md` stays lightweight and reports review tooling.
- Base: an ordinary runtime Python diff remains application-required.
- Bad: a docs-only manual dispatch remains lightweight, or a non-`full-ci`
  label on an armed PR rebuilds the required context from the quick lane.

### 6. Tests Required

- `tests/test_ci_change_classifier.py` asserts pack/audit positive cases and
  runtime/dependency/workflow negative cases.
- `tests/test_ci_review_contract.py` mutation-tests the labeled auto-merge
  clause, manual-dispatch force-app, every managed-Python lightweight guard
  command, and both syntax lists against the live repository.
- `tests/test_python_syntax_lint.py` parses all tracked Python under
  `scripts/`, `src/`, `tests/`, `tools/`, and generated hook roots.

### 7. Wrong vs Correct

Wrong:

```bash
# Manual dispatch can leave a docs-only diff app_required=false.
bash scripts/classify-ci-changes.sh --github-output changed-files.txt

# Any non-full-ci label ignores the armed PR state.
if [ "$PR_LABEL" = "full-ci" ]; then
  full_ci_requested=true
fi
```

Correct:

```bash
classifier_args=(--github-output)
if [ "$EVENT_NAME" = "workflow_dispatch" ]; then
  classifier_args+=(--force-app)
fi

if [ "$PR_LABEL" = "full-ci" ] || [ "$PR_AUTO_MERGE" = "true" ]; then
  full_ci_requested=true
fi
```

Sources: `.github/workflows/ci.yml`; `.pre-commit-config.yaml`;
`scripts/classify-ci-changes.sh`; `tools/check_ci_review_contract.py`;
`tests/test_ci_change_classifier.py`; `tests/test_ci_review_contract.py`;
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

The weekly command-pack sync must run the canonical installer from the
`platypeeps/sd-ai-command-pack` `main` branch, refresh
`docs/repomix-map.md`, and use one fixed PR branch. The no-diff path must create
neither a branch nor a PR; a real diff must open or update a PR and arm the
normal squash auto-merge path without a direct `main` push or `--admin`
bypass. Both write steps must use the scoped
`SD_AI_COMMAND_PACK_PR_TOKEN` Actions secret; the repo-wide Actions
create/approve setting and `GITHUB_TOKEN` must stay out of this path. Sources:
`.github/workflows/sd-ai-command-pack-sync.yml`;
`tools/check_ci_review_contract.py`; `tests/test_ci_review_contract.py`.

Windows portability coverage is collection-only and advisory: pull requests
sync the locked Python 3.14 development environment on `windows-latest`, then
run `pytest --collect-only -q`. The job must use `continue-on-error: true` and
must not appear in the `test` or `CI Result` dependency lists. Sources:
`.github/workflows/ci.yml`; `tools/check_ci_review_contract.py`;
`tests/test_ci_review_contract.py`; `CLAUDE.md`;
`docs/DEVELOPMENT_CYCLE.md`.

## Review Checklist

Before marking a PR ready, walk these headings: scope and description,
validators/schema, docs/docstrings, single source of truth, completeness,
mode/flag combinations, deterministic test paths, hot-path performance,
user-facing output order, test hygiene, test resource cost, cross-platform
guards, default-behavior changes, and CI/workflow/dependency hygiene. Sources:
`CLAUDE.md`; `.github/PULL_REQUEST_TEMPLATE.md`;
`.github/instructions/anomaly-metric-creator.instructions.md`.

When a recurring issue is mechanical and greppable, prefer a `tools/check_*.py`
lint plus tests over prose-only rules. Sources: `CLAUDE.md`;
`.pre-commit-config.yaml`; `tests/test_role_name_leaks_lint.py`;
`tests/test_branch_name_lint.py`; `tests/test_ruff_lockstep_lint.py`;
`tests/test_workflow_pip_lint.py`.

Treat Copilot and AI review comments as actionable by default, but verify them
against current `HEAD`, code comments, and actual trust boundaries before
fixing. Sources: `CLAUDE.md`;
`.github/instructions/anomaly-metric-creator.instructions.md`.

## Verification Commands

Common local checks are:

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check tests/
.venv/bin/pre-commit run --all-files
git diff --check
```

Run the narrowest focused regression first, then affected files/suites, then
broader checks when blast radius warrants it. Sources: `CLAUDE.md`; `README.md`;
`pyproject.toml`; `.pre-commit-config.yaml`; `.github/workflows/ci.yml`;
`tests/`.
