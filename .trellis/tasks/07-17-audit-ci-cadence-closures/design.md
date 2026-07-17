# Close CI cadence and lint-mirror gaps — Design (SD Work Designs, 2026-07-17)

## Overview

Twelve ledger items + one session-discovered classifier gap, all in the
CI/tooling plane. Every fix is already sketched per-item in the ledger;
the design work is slicing them into safe PRs, fixing the shared contract
anchors, and sequencing so each PR's own CI proves its change.

## Proposal — three PRs, riskiest-first

### PR 1 — workflow correctness (A-047, A-051, A-053, A-049 + addendum)

The items that change *which lane runs*:

- A-047: the `labeled` arm in ci.yml (:104-108) computes
  `full_ci_requested` without consulting `PR_AUTO_MERGE` — honor it, and
  add a `check_ci_review_contract.py` positive anchor pinning the
  expression so a refactor can't silently drop it again.
- A-051: `workflow_dispatch` passes `--force-app` to the classifier.
- A-053: the lightweight guards step gets `setup-uv` + `uv run --python
  3.14` instead of bare system `python`.
- A-049 + addendum: `is_lightweight_path()` gains `.sd-ai-command-pack/*`
  (as review-tooling) and `.trellis/audit/*`; `toolchain.sh` + the shell
  lib join both `bash -n` lists; py-syntax globs widen to `scripts/*.py`.
- Every classifier change lands with a `tests/` case mirroring the
  existing classifier tests (the acceptance addendum's assertion).

### PR 2 — lint mirrors + local parity (A-048, A-050, A-052, A-060, A-061, A-062)

- A-048: `tools/check_mypy_gate.py` owns the 19-module list; ci.yml calls
  it; documented in DEVELOPMENT_CYCLE for local preflight. The list moves
  *out* of YAML — one source of truth (repo's own single-source rule).
- A-060: the guards step invokes role-name / amc-module-load /
  agent-hook-exceptions (sub-second each).
- A-052: role-name live-tree scan roots extend to `src/`, `scripts/`,
  `.agents/`, `.trellis/` (watch: `.trellis/audit/ledger.md` and task
  PRDs must stay clean — they are, by construction; the lint's allow
  marker exists for the lint's own fixtures).
- A-061: changes job runs `check_branch_name.py` against
  `github.head_ref` (closes the documented refspec bypass in CI).
- A-062: `stages: [commit-msg]` hook entry + install-instructions note in
  CLAUDE.md.
- A-050: full-check preflight — `python3` fallback when node is absent
  (or hard-require node; pick fallback: weaker but non-blocking for the
  pack's portability posture — record which in the PR).

### PR 3 — new automation (A-063, A-065)

- A-063: scheduled pack-sync workflow (weekly), PR-on-change only,
  reusing the existing auto-merge gate. **Standing automation — confirm
  with the maintainer before merging this arm** (it creates recurring
  PRs); the workflow itself is repo-local, no upstream changes.
- A-065: `windows-latest` collect-only job (`uv sync --extra dev` +
  `pytest --collect-only`) — advisory, not a required context, so a
  Windows-runner flake cannot block merges.

## Boundaries And Non-Goals

- No cadence redesign; the path-classified three-lane model stays.
- CodeQL's persistent-label semantics untouched (contract-pinned).
- The pack's own upstream files are not edited — A-049/A-050 touch only
  repo-owned classifier/entry scripts; if a fix belongs upstream in the
  pack, write the paste-ready handoff instead.

## Affected Files

`.github/workflows/ci.yml`, `scripts/classify-ci-changes.sh` (+ tests),
`tools/check_ci_review_contract.py`, new `tools/check_mypy_gate.py`,
`.pre-commit-config.yaml` (commit-msg stage),
`tests/test_role_name_leaks_lint.py` scan roots, new pack-sync workflow,
docs (DEVELOPMENT_CYCLE, CLAUDE.md), `.trellis/audit/ledger.md` flips.

## Risks And Edge Cases

- ci.yml edits classify as workflow diffs → every one of these PRs runs
  the full matrix — the change proves itself, but bundle reviews
  accordingly (that is why PR 1 groups all lane-selection edits).
- A-047's mutation test: simulate the labeled event against an armed PR
  in the contract checker's fixture mode rather than live CI.
- A-053/A-060 must keep the lightweight lane fast (<1 min budget) —
  measure in the PR.
- Windows collect-only will surface any unguarded POSIX import at
  collection (that is its purpose) — expect and fix within PR 3, guarded
  per CLAUDE.md's cross-platform rules.

## Validation

- Per PR: full matrix runs by construction; classifier `tests/` cases;
  `check_ci_review_contract.py` green; for PR 2, run each mirrored lint
  locally and in the CI logs.
- A-049 addendum proof: a scratch branch touching only
  `.trellis/audit/**` classifies lightweight in the classifier test.
