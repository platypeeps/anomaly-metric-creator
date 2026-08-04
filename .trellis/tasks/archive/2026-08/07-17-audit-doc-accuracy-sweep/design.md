# Stale security/reviewer/reference docs — Design (SD Work Designs, 2026-07-17)

## Overview

Eight items, all doc-vs-code drift (the repo's #1 review pattern), one of
which (A-046) is a deliberate manifest change riding along. Per-item
evidence and target text are in the ledger; the design fixes the sweep
method, the one genuinely behavioral rider, and the anchor strategy so
the worst two (SECURITY.md posture, Copilot flags) cannot silently rot
again.

## Proposal

One PR, two commits (docs sweep; manifest floors) so the A-046 rider is
individually revertable.

**Commit 1 — docs:**

- A-026 SECURITY.md: rewrite :86-91 to the shipped dual posture
  (response side mask-unless-known-safe with the
  `_SAFE_RESPONSE_HEADER_NAMES` allowlist; request side
  allowlist-of-sensitive) — source of truth is `redaction.py:17-58` and
  CLAUDE.md's redaction section; drop the completed-task pointer.
- A-027 Copilot instructions: remove the five phantom flags
  (`--topology-mode`, `--validate-output`, `--combine-only`,
  `--emit-selection`, `independent` mode); restate the canonical surface
  (subcommands + `--emit`/`--otel-send`/`--otel-endpoint`); add
  `check_copilot_instruction_contract` anchors for the statements most
  likely to drift (flag names, required-context name).
- A-028 pyproject comments: mypy gate + 85% ratchet reality; live task
  pointer.
- A-029 CLAUDE.md: `test` aggregate *feeds* the required `CI Result`.
- A-030 README dev-extra list: complete it (mypy, pytest-cov, protobuf
  pair, pyyaml) or reword to "highlights include".
- A-064 README dev setup: `uv sync --extra dev --locked` primary; pip
  path demoted to alternative.
- A-069 README OTEL table: add `MEZMO_OTEL_STREAM_AUTH_SCHEME` row
  (default from `DEFAULT_OTEL_STREAM_AUTH_SCHEME`, legacy.py:7990-7992).

**Commit 2 — A-046 floors:** raise `numpy`/`protobuf` (and any sibling)
floors to the oldest combination actually exercised under py3.14 — read
`uv.lock` resolved versions (numpy 2.5.1, protobuf 7.35.1) and pick
deliberate floors at or below them that are py3.14-installable (numpy's
first 3.14-supporting release is the natural floor). State the chosen
floors + rationale in the PR; this is a Dependabot-visible manifest
change under `lockfile-only` — exactly the kind that must be explicit.

## Boundaries And Non-Goals

- No behavior changes beyond the floors; no CI edits (ci-cadence task
  owns those); no CHANGELOG restructure (release task owns it — but the
  floor raise gets an Unreleased line since it is user-facing).

## Affected Files

SECURITY.md, `.github/instructions/anomaly-metric-creator.instructions.md`,
`tools/check_copilot_instruction_contract.py` (anchors), pyproject.toml
(comments + floors), CLAUDE.md, README.md, CHANGELOG.md (floor line),
`.trellis/audit/ledger.md` flips (A-026…A-030, A-046, A-064, A-069).

## Risks And Edge Cases

- Grep sweeps must use the stale literal, not the symbol: e.g. search
  `--topology-mode` repo-wide — CLAUDE.md legitimately documents it as
  *removed*; the sweep target is docs presenting it as *current*.
- The contract-checker anchors must be full-token/anchored (repo's
  anchored-matching rule) so `--emit` vs `--emit-selection` cannot
  false-pass.
- Floor raise + `uv.lock`: floors ≤ locked versions keeps the lock valid;
  run `uv lock --check` to prove no resolution change.

## Validation

- Per-item grep sweep list in the PR description, each returning empty
  (or only removed-flag historical mentions).
- `uv lock --check`; full suite (floors only affect install-time).
- `tools/check_copilot_instruction_contract.py` green with new anchors;
  mutation-check one anchor by re-introducing a phantom flag locally.
