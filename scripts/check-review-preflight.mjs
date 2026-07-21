#!/usr/bin/env node
import { existsSync } from "node:fs";
import { spawnSync } from "node:child_process";

const python = process.env.REVIEW_PREFLIGHT_PYTHON || (existsSync(".venv/bin/python") ? ".venv/bin/python" : "python3");

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
run("Clean-module mypy gate", python, ["tools/check_mypy_gate.py"]);

// Contract mutation suites stay in CI; the real-repo guards above validate the
// checkout directly:
// tests/test_copilot_instruction_contract.py
// tests/test_pr_body_scope_lint.py
