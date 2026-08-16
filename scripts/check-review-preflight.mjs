#!/usr/bin/env node
import { existsSync } from "node:fs";
import { spawnSync } from "node:child_process";

const python = process.env.REVIEW_PREFLIGHT_PYTHON || (existsSync(".venv/bin/python") ? ".venv/bin/python" : "python3");

const LAYOUT_RESOLVER = ".sd-ai-command-pack/bin/sd-ai-command-pack-review-layout.py";

// Since the thin conversion the pack's own scripts are not in this tree; they
// live wherever the machine keeps the install, so the resolver -- not a path --
// says where. Returning null when nothing answers is deliberate: a checkout
// without an install (a CI runner, a fresh clone) skips the pack-owned guard
// rather than failing on a file nobody shipped to it.
function resolvePackScript(name) {
  if (!existsSync(LAYOUT_RESOLVER)) return null;
  const result = spawnSync("python3", [LAYOUT_RESOLVER, "--resolve", name], { encoding: "utf8" });
  if (result.status !== 0 || !result.stdout) return null;
  let resolved;
  try {
    resolved = JSON.parse(result.stdout);
  } catch {
    return null;
  }
  const resolvedPath = resolved?.path;
  if (typeof resolvedPath !== "string" || resolvedPath === "" || !existsSync(resolvedPath)) return null;
  return resolvedPath;
}

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

const prBodyScopeGuard = resolvePackScript("sd-ai-command-pack-pr-body-scope.py");
if (prBodyScopeGuard) {
  run("PR body scope guard", python, [prBodyScopeGuard]);
} else {
  console.log("\n==> PR body scope guard");
  console.log("skipped: no resolvable sd-ai-command-pack install provides the PR body scope guard");
}

run("Clean-module mypy gate", python, ["tools/check_mypy_gate.py"]);

// The contract mutation suite stays in CI; the real-repo guards above validate
// the checkout directly:
// tests/test_copilot_instruction_contract.py
