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
`tests/test_correctness.py`; `tests/test_gauges_file.py`.

Tests that use POSIX-only modules or attributes must guard collection on
platforms where those APIs are missing. Sources: `CLAUDE.md`; `tests/`;
`.github/workflows/ci.yml`.

## Pytest, Ruff, and Pre-Commit

The default pytest invocation uses xdist loadfile distribution with four
workers: `addopts = "-ra --dist loadfile -n 4"`, and `required_plugins`
requires `pytest-xdist` so missing xdist fails clearly. Sources:
`pyproject.toml`; `README.md`; `CLAUDE.md`; `tests/conftest.py`;
`.github/workflows/ci.yml`.

Use `-n 0` for true in-process serial runs such as `pdb`; `-n 1` still spawns
an xdist worker subprocess. Sources: `README.md`; `CLAUDE.md`;
`pyproject.toml`.

The `heavy` marker is auto-applied by `tests/conftest.py` based on fixture
closure, not hand-written on tests. CI runs heavy tests serially and the
non-heavy set under xdist to stay inside runner memory while still exercising
parallel ordering. Sources: `tests/conftest.py`; `pyproject.toml`;
`.github/workflows/ci.yml`; `CLAUDE.md`; `README.md`;
`tests/test_heavy_marker.py`.

Ruff F401 is selected in `pyproject.toml` and scoped to tests by
`.pre-commit-config.yaml`; run `.venv/bin/ruff check tests/` or
`.venv/bin/pre-commit run --all-files` for touched test hygiene. Sources:
`pyproject.toml`; `.pre-commit-config.yaml`; `README.md`; `CLAUDE.md`;
`.github/workflows/ci.yml`.

Additional mechanical guards catch recent review-churn patterns before PR
review: syntax-only `ast.parse` over Python files, Ruff F841 unused locals for
runtime/tools/hooks, agent-hook exception-shape checks, Trellis placeholder
and journal/index commit-list consistency checks, Copilot instruction contract
checks, and trace-payload validation anti-pattern checks. Keep these hooks
stdlib-only where they are local scripts, with the documented `0`/`1`/`2` exit
contract and acceptance tests over both temporary fixtures and the live repo
tree. Sources: `.pre-commit-config.yaml`;
`tools/check_python_syntax.py`;
`tools/check_agent_hook_exceptions.py`; `tools/check_trellis_placeholders.py`;
`tools/check_copilot_instruction_contract.py`;
`tools/check_trace_payload_antipatterns.py`;
`tests/test_python_syntax_lint.py`;
`tests/test_agent_hook_exception_lint.py`;
`tests/test_trellis_placeholder_lint.py`;
`tests/test_copilot_instruction_contract.py`;
`tests/test_trace_payload_antipatterns_lint.py`.

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
scope guard, and focused review-churn tests. Use
`SD_AI_COMMAND_PACK_FULL_CHECK_PRISM=0` or
`SD_AI_COMMAND_PACK_FULL_CHECK_GITO=0` to skip optional AI review while
iterating. Use `SD_AI_COMMAND_PACK_FULL_CHECK_PRISM_FAIL_ON`,
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
`test`, while `scripts/classify-ci-changes.sh` selects the cheapest safe lane:
lightweight readiness for docs/spec/agent/review-tooling-only changes, quick
test for ordinary PR update churn that still touches app paths, and the full
Python 3.12 test lane for app-required opened/reopened/ready PRs,
`full-ci` label runs,
workflow/dependency changes, manual dispatch, and `main` pushes. Python 3.12
is the only version CI tests against; `requires-python >=3.11` remains the
declared floor without a dedicated CI lane. Sources:
`.github/workflows/ci.yml`; `scripts/classify-ci-changes.sh`;
`tools/check_ci_review_contract.py`; `tests/test_ci_change_classifier.py`;
`tests/test_ci_review_contract.py`; `docs/DEVELOPMENT_CYCLE.md`.

CodeQL must run on PR updates because branch protection requires the GitHub
Advanced Security `CodeQL` context on the latest commit; do not remove the
`synchronize` trigger unless branch protection is changed in the same rollout.
Socket should keep a visible PR check but fast-skip unless
dependency/security-relevant files changed or full CI was requested. Sources:
`.github/workflows/codeql.yml`; `.github/workflows/socket.yml`;
`scripts/classify-ci-changes.sh`; `tools/check_ci_review_contract.py`;
`docs/DEVELOPMENT_CYCLE.md`.

Dependabot auto-merge should enable GitHub auto-merge for patch/minor updates
without trying to approve the pull request with `GITHUB_TOKEN`, because this
repo's workflow token is not allowed to create PR reviews. Sources:
`.github/workflows/dependabot-auto-merge.yml`;
`tools/check_ci_review_contract.py`; `tests/test_ci_review_contract.py`.

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
