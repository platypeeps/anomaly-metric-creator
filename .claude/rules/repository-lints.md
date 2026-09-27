---
paths:
  - "tools/**"
  - "scripts/**"
  - ".pre-commit-config.yaml"
  - ".github/workflows/**"
---

# Repository lints

Each guard documents its full contract in its module docstring: pattern, modes, escape hatches, and the `0` clean / `1` violation / `2` structural-error exit split.
Read the script, not a copy of it.
CI lane coverage for every guard: [testing-quality.md](../../docs/spec/amc/backend/testing-quality.md).

| Guard | Enforces |
| --- | --- |
| `tools/check_role_name_leaks.py` | internal role-name references in text-bearing files; stdin `-` mode pre-flights a comment body |
| `tools/check_approval_duplicate.py` | duplicate or self-correction `APPROVED` PR comments, keyed on (author, head commit) |
| `tools/pr_comment.sh` | the canonical wrapper: runs both comment gates, then `gh pr comment` |
| `tools/check_branch_name.py` | branch names that republish an internal ticket literal (`pre-push`; install with `pre-commit install --hook-type pre-push`) |
| `tools/check_ruff_lockstep.py` | the `ruff==` pin in `pyproject.toml` against the ruff-pre-commit `rev` |
| `tools/check_csv_formula_trigger_lockstep.py` | `trace_bundle._CSV_FORMULA_TRIGGERS` against the debug UI's marked `csvCell` guard, two independent export paths |
| `tools/check_repomix_map_freshness.py` | every path in the generated `docs/repomix-map.md` still resolving; runs `always_run` because other files moving makes it stale |
| `tools/check_workflow_pip.py` | bare or unpinned `pip install` in workflows |
| `tools/check_test_resource_cost.py` | whole-file reads of generated CSVs under `tests/` |
| `tools/check_amc_module_load.py` | direct `spec_from_file_location` loads of `legacy.py` in tests |
| `tools/check_mypy_gate.py` | the canonical clean-module mypy gate command and list |
| `tools/check_module_size.py` | the ratcheted 800-line behavior-module cap; `--list` prints the enrolled table |
| `tools/check_ci_review_contract.py` | CI cadence, action pins, partition commands, aggregate guards |
| `tools/check_copilot_instruction_contract.py` | checklist-heading lockstep across the spec, template, and Copilot instructions |
| `tools/check_task_criteria_commands.py` | quoted acceptance-criteria commands in `docs/work/**/*.md` that cannot produce the output they claim |
| `tools/check_guard_ci_coverage.py` | every `tools/check_*.py` running in each CI lane (LIGHT / QUICK / FULL) its watched files can select; `--list` prints the table |
| `tools/check_work_item_placeholders.py`, `tools/check_python_syntax.py`, `tools/check_trace_payload_antipatterns.py` | placeholder, syntax, and trace-payload shapes |

- The repomix map omits exactly two tracked paths, itself and `uv.lock`; a tracked file absent from the map is out of the freshness guard's scope by design.
- An archive commit that moves a `docs/work/` directory carries the regenerated map; the freshness guard fails otherwise.
- `tools/benchmark_combine.py` is the one tool without tests; it is a measurement harness, not a lint.
