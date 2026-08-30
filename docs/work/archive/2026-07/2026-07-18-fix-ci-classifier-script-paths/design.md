# CI classifier script paths — Design (SD Work Designs, 2026-07-18)

## Overview

`scripts/update_repomix` triggered the 16.6-minute full matrix on PR #249
because the classifier treats anything outside its lightweight allowlist as
application-required. The implementation-day enumeration shows the class is
25 paths split into three groups with **different correct answers**; blanket
classification would open a real coverage gap.

## Enumeration

Every tracked path under `scripts/`, `tools/`, and `docs/` ran through
`scripts/classify-ci-changes.sh`. Twenty-five came back `app_required`
(`docs/` was already fully classified):

**Group A — repo/pack tooling with no skipped behavioral test (7):**

```
scripts/sd-ai-command-pack-record-session.py
scripts/sd-ai-command-pack-review-learnings.py
scripts/sd-ai-command-pack-status.py
scripts/sd-ai-command-pack-update-spec-kb.py
scripts/sd-ai-command-pack-work-loop.py
scripts/sd_ai_command_pack_fleet_lib.py
scripts/update_repomix
```

**Group B — command-pack scripts retained as application-required (3):**

```
scripts/sd-ai-command-pack-review-full-check.sh
scripts/sd_ai_command_pack_lib.py
scripts/sync-agent-skills.py
```

The shared library and sync command have behavioral coverage. The review
wrapper is conservatively retained until the always-run shell syntax guard
covers it.

**Group C — all `tools/` paths stay application-required (15):**

```
tools/benchmark_combine.py            tools/check_python_syntax.py
tools/check_agent_hook_exceptions.py  tools/check_role_name_leaks.py
tools/check_amc_module_load.py        tools/check_ruff_lockstep.py
tools/check_approval_duplicate.py     tools/check_trace_payload_antipatterns.py
tools/check_branch_name.py            tools/check_trellis_placeholders.py
tools/check_ci_review_contract.py     tools/check_workflow_pip.py
tools/check_copilot_instruction_contract.py
tools/check_mypy_gate.py              tools/check_test_resource_cost.py
```

## Proposal

**Classify Group A lightweight. Leave Groups B and C alone.**

Group C is the trap. Each `tools/check_*.py` has a corresponding
`tests/test_*_lint.py`, and the lightweight lane does not run tests — it
runs a fixed set of guards. Classifying `tools/check_role_name_leaks.py` as
lightweight would skip `tests/test_role_name_leaks_lint.py` on the very PR
that changed it. Today those paths correctly land in `app_required`, and the
quick lane runs 9 of the lint test files (`ci.yml:260-269`). That is the
right behavior; do not "fix" it.

`tools/benchmark_combine.py` is the one Group C member with no test — it is
a benchmark harness, not a check. It could move to Group A, but the gain is
one rarely-edited file against the cost of a special case in an otherwise
clean "all of `tools/` is app-required" rule. **Recommend leaving it**, and
recording the reason so it is not re-raised.

**The governing rule**, which is what makes this reviewable and keeps the
next addition correct:

> Classify a path lightweight only if **no test would be skipped** by doing
> so. If the path has test coverage, either leave it app-required or add
> that test to the lightweight lane's guard steps.

**Mechanism.** Add a sibling predicate `is_repo_tooling_path` rather than
extending `is_review_tooling_path`. The latter has a specific meaning —
review/pack tooling that also sets `review_tooling_changed` — and
`scripts/update_repomix` is not review tooling. A separate predicate called
from `is_lightweight_path` (`classify-ci-changes.sh:96-116`) keeps both
names honest.

The live verification moved `scripts/sd_ai_command_pack_lib.py` and
`scripts/sync-agent-skills.py` out of Group A because their behavior is covered
by tests. Textual contract checks that merely require a script name do not
exercise that script's behavior and therefore do not disqualify Group A.

## Boundaries And Non-Goals

- No change to the dependency/workflow escalation
  (`classify-ci-changes.sh:252-255`). `pyproject.toml`, `uv.lock`,
  `.pre-commit-config.yaml`, and `.github/workflows/*` must keep forcing the
  full matrix and the Socket re-scan.
- No change to lane definitions, triggers, or the `full-ci` label semantics
  (`tools/check_ci_review_contract.py` pins the deliberate asymmetry between
  `ci.yml` one-shot and `codeql.yml` persistent).
- No blanket `scripts/*` or `tools/*` glob. The whole point is that the two
  directories differ.

## Affected Files

`scripts/classify-ci-changes.sh` (new predicate + call site),
`tests/test_ci_change_classifier.py` (a case per path plus negatives),
`CLAUDE.md` CI-cadence section.

## Risks And Edge Cases

- **Over-classification is the failure mode to fear.** A misclassified path
  means an application regression ships with no test run. Under-classifying
  costs 16 CI minutes on a public repo where minutes are free. The
  asymmetry justifies being conservative at every boundary.
- **`scripts/classify-ci-changes.sh` classifies itself** via
  `is_review_tooling_path` (`:72`). Adding a predicate to that file changes
  its own classification path; confirm a self-edit still escalates
  appropriately.
- **`is_dependency_path` runs first and wins** — a `package.json` anywhere
  still forces the full matrix regardless of the new predicate. Preserve
  that precedence.
- **Group A members are mostly pack-managed.** Classifying them is a
  repo-local classifier change and does not touch the files themselves, so
  no provenance drift. Worth stating in review to avoid the question.

## Validation

- Every Group A path returns `lightweight_only=true` / `app_required=false`.
- Every Group B path still returns `app_required=true`.
- `src/anomaly_metric_creator/*` still returns `app_required=true`.
- `pyproject.toml` and `.github/workflows/*` still set
  `dependency_changed` / `workflow_changed`.
- A mixed diff (one Group A path plus one application path) still escalates.
