#!/usr/bin/env node
import { existsSync } from "node:fs";
import { spawnSync } from "node:child_process";

const python = process.env.REVIEW_PREFLIGHT_PYTHON || (existsSync(".venv/bin/python") ? ".venv/bin/python" : "python3");
const pytestCommand = process.env.REVIEW_PREFLIGHT_PYTEST || (existsSync(".venv/bin/pytest") ? ".venv/bin/pytest" : python);
const pytestPrefixArgs = pytestCommand === python ? ["-m", "pytest"] : [];

function run(label, command, args) {
  console.log(`\n==> ${label}`);
  const result = spawnSync(command, args, {
    stdio: "inherit",
    env: process.env,
  });

  if (result.error) {
    console.error(`review preflight: ${label} failed to start: ${result.error.message}`);
    process.exit(127);
  }
  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
}

run("CI/review cadence contract guard", python, ["tools/check_ci_review_contract.py"]);
run("Copilot instruction contract guard", python, ["tools/check_copilot_instruction_contract.py"]);
run("PR body scope guard", python, ["scripts/sd-ai-command-pack-pr-body-scope.py"]);
run("Review-churn lint tests", pytestCommand, [
  ...pytestPrefixArgs,
  "-q",
  "tests/test_ci_change_classifier.py",
  "tests/test_python_syntax_lint.py",
  "tests/test_workflow_pip_lint.py",
  "tests/test_ci_review_contract.py",
  "tests/test_copilot_instruction_contract.py",
  "tests/test_pr_body_scope_lint.py",
  "tests/test_ruff_lockstep_lint.py",
  "tests/test_trellis_placeholder_lint.py",
  "tests/test_trace_payload_antipatterns_lint.py",
]);
