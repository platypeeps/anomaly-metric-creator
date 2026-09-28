#!/usr/bin/env bash
# `make check`: every step `.github/workflows/ci.yml` runs toward the required
# `CI Result`, on this machine, plus the local deterministic gate
# (`pre-commit run --all-files` and `scripts/check-review-preflight.mjs`).
# `sd-check` finds it through the Makefile; `sd-ship merge` runs it through the
# command pack's local gate when this repository's `repo.ci` is `local`.
#
# CI picks one lane from the changed paths. This runs the union: the `changes`
# guards, `lightweight readiness`, and the full lane (`test heavy`,
# `test light`, `coverage`). The quick lane's pytest list is a subset of the
# full suite, so it adds nothing here.
#
# Not run, and why:
# - pinned real kubectl/Helm smokes: CI downloads linux-amd64 binaries by
#   checksum; the two tests still collect here and skip without the opt-in.
# - report-only mypy baseline: `continue-on-error` in CI, so it never gates;
#   the clean-module mypy gate does run.
# - Windows collection: advisory, outside `CI Result`, needs Windows.
# - Socket scan: needs the SOCKET_SECURITY_API_KEY repository secret.
#
# The gate puts no virtualenv on PATH, so this builds `.venv-check` from
# `uv.lock` (`uv sync --extra dev --locked`), as CI does. `.venv-check*/` is
# gitignored. BASE is the ref the whitespace check diffs against.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

PYTHON_VERSION=3.14
BASE="${BASE:-origin/main}"
# CI runs two workers per partition to fit a hosted runner's memory; locally
# pyproject's `-n 4` is the measured throughput default.
WORKERS="${CHECK_PYTEST_WORKERS:-4}"

export UV_PROJECT_ENVIRONMENT=.venv-check
unset VIRTUAL_ENV PYTHONPATH CONDA_PREFIX
PY=.venv-check/bin/python

step() { printf '\n==> %s\n' "$1"; }

# Command substitution throughout, never `done < <(git ...)`, for the reason
# ci.yml gives: a git failure must abort instead of yielding an empty list.
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

# --- changes: branch name and lightweight repository guards ----------------

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
"$PY" tools/check_guard_ci_coverage.py

# --- lightweight readiness -------------------------------------------------

step "Whitespace check against $BASE"
base=$(git merge-base "$BASE" HEAD) || { echo "check: no merge base with $BASE; fetch it first" >&2; exit 1; }
git diff --check "$base" HEAD

step "Shell syntax for scripts"
tracked 'scripts/*.sh' scripts/update_repomix
for script in "${files[@]}"; do
  bash -n "$script"
done

step "Smoke CI classifier"
smoke=$(mktemp -d)
trap 'rm -rf "$smoke"' EXIT
printf '%s\n' \
  'docs/REVIEW_PATTERNS.md' \
  '.github/instructions/anomaly-metric-creator.instructions.md' \
  '.prism/rules.json' > "$smoke/changes.txt"
bash scripts/classify-ci-changes.sh "$smoke/changes.txt" > "$smoke/classifier.out"
grep -q '^lightweight_only=true$' "$smoke/classifier.out"
grep -q '^app_required=false$' "$smoke/classifier.out"

step "Syntax and artifact guards"
tracked 'scripts/*.py' 'tools/*.py' 'tests/*.py' '.codex/hooks/*.py' '.github/copilot/hooks/*.py' '.gemini/hooks/*.py'
[ "${#files[@]}" -gt 0 ] || { echo "No Python files matched." >&2; exit 1; }
"$PY" tools/check_python_syntax.py "${files[@]}"
tracked '.github/workflows/*.yml' '.github/workflows/*.yaml'
[ "${#files[@]}" -gt 0 ] || { echo "No workflow files matched." >&2; exit 1; }
"$PY" tools/check_workflow_pip.py "${files[@]}"
listing=$(git ls-files docs/work)
files=()
while IFS= read -r path; do
  case "$path" in *.md) files+=("$path") ;; esac
done <<< "$listing"
if [ "${#files[@]}" -gt 0 ]; then
  "$PY" tools/check_work_item_placeholders.py "${files[@]}"
fi
"$PY" tools/check_ci_review_contract.py
"$PY" tools/check_copilot_instruction_contract.py

# --- full lane: test light, test heavy, coverage ---------------------------

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
