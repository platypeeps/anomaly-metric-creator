---
title: Classify non-application script paths out of the full CI matrix
status: done
created: 2026-07-18
---
# Classify non-application script paths out of the full CI matrix

## Goal

`scripts/update_repomix` is a helper that regenerates a docs map. Changing
it on PR #249 triggered the **16.6-minute full test matrix**, because the
classifier treats any path outside its lightweight allowlist as
application-required. The classifier is otherwise well-shaped; it just has
no notion of "repo tooling that is neither review tooling nor application
code".

## Measurement context

- Run `29630578563` (PR #249, `codex/stabilize-repomix-map-order`):
  **16.6 min**, `test (py3.14)` = 16m07s, for a diff of
  `docs/repomix-map.md`, `scripts/update_repomix`, two Trellis workspace
  files, and one spec file.
- Every other file in that diff is already lightweight
  (`docs/*.md`, `.trellis/*`); `scripts/update_repomix` alone escalated it.
- `scripts/classify-ci-changes.sh:70-94` — `is_review_tooling_path` lists
  each pack and review script by exact name; `scripts/update_repomix` is
  not among them.
- `scripts/classify-ci-changes.sh:151-154` — anything not matching
  `is_lightweight_path` sets `app_required=true`.

The implementation-day re-enumeration found 25 application-required paths,
not the planning snapshot's 21. Seven repo-only scripts have no behavioral
test that the lightweight lane would skip and are safe to classify there.
Three other command-pack scripts remain application-required:
`scripts/sd_ai_command_pack_lib.py` and `scripts/sync-agent-skills.py` have
direct consumers/tests, while `scripts/sd-ai-command-pack-review-full-check.sh`
is conservatively retained until the always-run shell-syntax guard covers it.
All 15 `tools/` paths remain application-required; this includes the untested
benchmark harness because a single directory-level rule is easier to audit.

## Requirements

- Add a classification for repo tooling that cannot affect application
  behavior. Weigh in `design.md` whether to extend `is_review_tooling_path`
  (simple, but the name stops being accurate) or add a sibling predicate
  such as `is_repo_tooling_path` (clearer, one more function).
- **Enumerate the candidates rather than fixing only the one that bit.**
  Audit every path under `scripts/`, `tools/`, and `docs/` that is not
  already classified, and decide each explicitly. The pre-PR checklist's
  completeness rule applies: a PR titled "classify non-application script
  paths" should cover the class, not the instance.
- Be conservative at the boundary. `tools/check_*.py` scripts are covered
  by tests in `tests/`, and a `.py` change already sets `python_changed`;
  make sure a lint script's own change still runs the tests that cover it.
  Misclassifying application-adjacent code as lightweight is a worse
  failure than an occasional over-run.
- Extend `tests/test_ci_change_classifier.py` with a case per newly
  classified path, asserting both `lightweight_only=true` and
  `app_required=false`, and a negative case proving an application path
  still escalates.
- Do not weaken the dependency/workflow escalation
  (`classify-ci-changes.sh:252-255`): a `pyproject.toml`, `uv.lock`,
  `.pre-commit-config.yaml`, or `.github/workflows/*` change must still
  force the full matrix and the Socket re-scan.

## Acceptance criteria

- [x] A diff touching only `scripts/update_repomix` classifies as
      `lightweight_only=true` / `app_required=false`.
- [x] Every unclassified path under `scripts/`, `tools/`, and `docs/` has
      been enumerated and explicitly decided; the PR lists them.
- [x] A diff touching `src/anomaly_metric_creator/*` still classifies as
      `app_required=true`.
- [x] A diff touching `pyproject.toml` or any workflow still sets
      `dependency_changed` / `workflow_changed` and forces the full matrix.
- [x] `tests/test_ci_change_classifier.py` covers each new path plus the
      negative cases above.
- [x] `CLAUDE.md`'s CI-cadence section describes the new classification.

## Non-goals

- Changing the lane definitions or their triggers.
- Changing the `full-ci` label semantics, which
  `tools/check_ci_review_contract.py` deliberately pins as asymmetric
  between `ci.yml` (one-shot) and `codeql.yml` (persistent).
