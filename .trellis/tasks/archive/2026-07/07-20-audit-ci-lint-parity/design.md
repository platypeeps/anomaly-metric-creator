# Mirror CI lints and local gates Design

## Overview

This child removes CI/local drift by giving the mypy gate one executable owner,
mirroring existing mechanical guards, and closing the branch-name and
commit-hook bypasses identified by the audit.

## Proposal

- Add `tools/check_mypy_gate.py` as the canonical module-list owner and call it
  from both CI and the local full-check/preflight surface.
- Reuse existing lint entrypoints in the lightweight guards step; do not copy
  their logic into YAML.
- Expand role-name scan roots through the checker configuration and its tests.
- Pass `github.head_ref` to the existing branch-name checker, and register the
  existing role-name checker as a `commit-msg` hook that receives the commit
  message filename.
- Apply the pending Ruff 0.15.22 update to both pin owners and regenerate the
  lockfile, preserving the existing lockstep checker as the executable
  contract.

## Boundaries And Non-Goals

- No lane-selection or path-classifier work from the preceding child.
- No weakening of lint exit codes or allow markers.
- No new required GitHub check context.

## Affected Files

`tools/check_mypy_gate.py`, `.github/workflows/ci.yml`,
`.pre-commit-config.yaml`, existing lint tools/tests, `pyproject.toml`,
`uv.lock`, `docs/DEVELOPMENT_CYCLE.md`,
`CLAUDE.md`, relevant Trellis spec text, and `.trellis/audit/ledger.md`.

## Data And Command Contracts

Every checker retains deterministic 0/1/2-style process behavior where already
defined. The mypy gate accepts one canonical module set. Branch validation uses
the actual PR head ref, not a merge ref or local fallback.

## Risks And Edge Cases

The wider role-name scan includes generated and planning text, so explicit test
fixtures/allow markers must remain narrow. Commit-msg hook registration must be
compatible with repositories where that stage is not yet installed.

## Validation

Run focused checker tests, pre-commit across all files, the Ruff lockstep lint,
and the live lightweight/full PR jobs.
