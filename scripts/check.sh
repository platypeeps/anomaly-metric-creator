#!/usr/bin/env bash
# `make check`: the merge gate for this repository. `sd-check` finds it
# through the Makefile; `sd-ship merge` runs it and posts `sd/local-gate`, the
# one required check, because this repository's `repo.ci` is `local`. GitHub
# Actions runs no workflow here.
#
# The steps: branch name and repository guards, readiness checks, the heavy
# and light test partitions with the coverage threshold, then
# `pre-commit run --all-files` and `scripts/check-review-preflight.mjs`.
# `tools/check_local_gate_contract.py` guards the named anchors below.
#
# Not run, and why:
# - real kubectl/Helm smokes: the two tests collect here and skip without
#   `AMC_RUN_REAL_CLIENT_SMOKE=1` and the clients on PATH.
#
# The gate puts no virtualenv on PATH, so this builds `.venv-check` from
# `uv.lock` (`uv sync --extra dev --locked`). `.venv-check*/` is gitignored.
# BASE is the ref the whitespace check diffs against.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

PYTHON_VERSION=3.14
BASE="${BASE:-origin/main}"
# pyproject's `-n 4` is the measured throughput default.
WORKERS="${CHECK_PYTEST_WORKERS:-4}"

export UV_PROJECT_ENVIRONMENT=.venv-check
unset VIRTUAL_ENV PYTHONPATH CONDA_PREFIX
PY=.venv-check/bin/python

step() { printf '\n==> %s\n' "$1"; }

# Command substitution throughout, never `done < <(git ...)`: a git failure
# must abort instead of yielding an empty list.
tracked() {
  local listing
  listing=$(git ls-files "$@")
  files=()
  while IFS= read -r path; do
    [ -n "$path" ] && files+=("$path")
  done <<< "$listing"
}

step "Sync dependencies (dev extra, locked)"
uv sync --extra dev --locked --python "$PYTHON_VERSION"

# --- branch name and repository guards --------------------------------------

step "Branch name"
"$PY" tools/check_branch_name.py --current

step "Lightweight repository guards"
tracked 'tests/*.py'
[ "${#files[@]}" -gt 0 ] || { echo "No tests/*.py matched." >&2; exit 1; }
"$PY" tools/check_amc_module_load.py "${files[@]}"
"$PY" tools/check_test_resource_cost.py "${files[@]}"
tracked src scripts .agents
[ "${#files[@]}" -gt 0 ] || { echo "No src/scripts/.agents files matched." >&2; exit 1; }
"$PY" tools/check_role_name_leaks.py "${files[@]}"
tracked 'docs/work/*.md' 'docs/work/**/*.md'
if [ "${#files[@]}" -gt 0 ]; then
  "$PY" tools/check_task_criteria_commands.py "${files[@]}"
fi
"$PY" tools/check_repomix_map_freshness.py

# --- readiness --------------------------------------------------------------

step "Whitespace check against $BASE"
base=$(git merge-base "$BASE" HEAD) || { echo "check: no merge base with $BASE; fetch it first" >&2; exit 1; }
git diff --check "$base" HEAD

step "Shell syntax for scripts"
tracked 'scripts/*.sh' scripts/update_repomix
for script in "${files[@]}"; do
  bash -n "$script"
done

step "Syntax and artifact guards"
tracked 'scripts/*.py' 'tools/*.py' 'tests/*.py' '.codex/hooks/*.py' '.github/copilot/hooks/*.py' '.gemini/hooks/*.py'
[ "${#files[@]}" -gt 0 ] || { echo "No Python files matched." >&2; exit 1; }
"$PY" tools/check_python_syntax.py "${files[@]}"
listing=$(git ls-files docs/work)
files=()
while IFS= read -r path; do
  case "$path" in *.md) files+=("$path") ;; esac
done <<< "$listing"
if [ "${#files[@]}" -gt 0 ]; then
  "$PY" tools/check_work_item_placeholders.py "${files[@]}"
fi
"$PY" tools/check_local_gate_contract.py
"$PY" tools/check_copilot_instruction_contract.py

# --- tests: heavy, light, coverage -----------------------------------------

step "Smoke installed console scripts"
rm -rf .venv-check-smoke
uv venv --quiet --python "$PYTHON_VERSION" .venv-check-smoke
uv pip install --quiet --python .venv-check-smoke/bin/python .
.venv-check-smoke/bin/amc --help > /dev/null
.venv-check-smoke/bin/anomaly-metric-creator --help > /dev/null

step "Ruff version lockstep"
uv run --no-sync python tools/check_ruff_lockstep.py

step "Lint tests (ruff F401)"
uv run --no-sync ruff check tests/

step "Lint runtime/tools (ruff F841)"
tracked 'src/*.py' 'tools/*.py' '.codex/hooks/*.py' '.github/copilot/hooks/*.py' '.gemini/hooks/*.py'
[ "${#files[@]}" -gt 0 ] || { echo "F841 enumeration matched no files." >&2; exit 1; }
uv run --no-sync ruff check --select F841 "${files[@]}"

step "Type-check gate (mypy, clean modules)"
uv run --no-sync python tools/check_mypy_gate.py

rm -f .coverage .coverage.heavy .coverage.light
export COVERAGE_CORE=sysmon

step "Heavy test partition"
uv run --no-sync pytest -n "$WORKERS" --dist loadfile -m heavy --cov=src/anomaly_metric_creator --cov-report=
mv .coverage .coverage.heavy

step "Light test partition"
uv run --no-sync pytest -n "$WORKERS" --dist loadfile -m "not heavy" --cov=src/anomaly_metric_creator --cov-report=
mv .coverage .coverage.light

step "Coverage threshold"
uv run --no-sync coverage combine
uv run --no-sync coverage report --fail-under=85

# --- local deterministic gate ----------------------------------------------

step "pre-commit run --all-files"
uv run --no-sync pre-commit run --all-files

step "Review preflight"
REVIEW_PREFLIGHT_PYTHON="$PY" node scripts/check-review-preflight.mjs

step "check passed"
