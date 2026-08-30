---
title: Enforce uv.lock in CI with locked sync
status: done
created: 2026-07-06
---
# Enforce uv.lock in CI with locked sync

## Review context

- **Source:** deep-dive tooling/CI review, 2026-07-06.
- **Confidence:** CONFIRMED.
- **Severity:** MEDIUM (was HIGH; the stale-lock half was fixed on
  2026-07-06 — see below).
- **Category:** supply chain / reproducibility.

## Goal

Make CI fail loudly on pyproject/uv.lock drift so the committed lock stays
authoritative.

## Problem

The original finding had two halves. The first — `uv.lock` was stale
(`mypy==2.1.0` and `pytest-cov` declared in the dev extra but absent from
the lock) — was **fixed on 2026-07-06**: the latest-Python-only change
bumped `requires-python`, which forced a `uv lock` regen; both packages
are now locked and `uv lock --check` passes. The second half remains: CI
runs `uv sync --extra dev` with **no `--locked`/`--frozen`** (quick lane
and full lane in `.github/workflows/ci.yml`), so any future drift silently
re-resolves in the runner instead of failing — the exact gap that let the
lock go stale unnoticed in the first place, and which voids
`dependabot.yml`'s "committed lock is the pinned-version source of truth"
contract whenever drift exists.

## Requirements

- Add `--locked` to both `uv sync` invocations in ci.yml so manifest/lock
  drift fails the job instead of silently re-resolving.
- Pin the `--locked` usage in `tools/check_ci_review_contract.py` (new
  anchor) with a mutation test in `tests/test_ci_review_contract.py`, so
  a revert is caught — same pattern as the `!cancelled()` guard anchor.
- Confirm Dependabot `versioning-strategy: lockfile-only` now sees and
  manages the full dev set (mypy/pytest-cov entered the lock in the
  2026-07-06 regen).

## Acceptance Criteria

- [x] CI fails when pyproject.toml and uv.lock disagree (contract-lint
      mutation test proves the anchor; optionally demonstrate with a
      temporary drift commit in the PR).
- [x] mypy==2.1.0 and pytest-cov resolve from the lock in CI logs.
- [x] `docs/DEVELOPMENT_CYCLE.md` / CLAUDE.md CI text mentions the locked
      sync if they describe the sync steps. — N/A: neither doc names the
      `uv sync` step (grep confirmed), so there is nothing to update.

## Resolution (2026-07-07)

`--locked` added to both `uv sync` invocations in `ci.yml` (quick lane
:210, full lane :270). New `check_ci_review_contract.py` anchor
"locked dependency sync" pins the substring `uv sync --extra dev --locked`
(matches both lanes); mutation test `test_removing_locked_sync_flag_fails`
in `tests/test_ci_review_contract.py` proves a revert to bare
`uv sync --extra dev` fails the lint. The minimal contract fixture gained a
matching `--locked` sync step so the baseline still passes. Verified
`uv sync --extra dev --locked` resolves cleanly against the committed lock
(mypy==2.1.0 + pytest-cov present since the 2026-07-06 regen). Contract
lint exits 0; 15 contract tests pass.

## Notes

- Small, mechanical, high leverage: it converts the lock from advisory to
  enforced.
