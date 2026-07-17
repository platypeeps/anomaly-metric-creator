# Real kubectl/Helm smokes in CI + K8s version bump — Design (SD Work Designs, 2026-07-17)

## Overview

The env-gated real-client smokes (tests/test_server.py:135-137,
`AMC_RUN_REAL_CLIENT_SMOKE`) run nowhere — no workflow sets the var or
installs binaries — so the headline kubectl/Helm-4 compatibility claim is
untested in CI (A-022). The facade advertises v1.29.4
(server_ops.py:4936-4938, 2834-2836 — two literal sites), outside ±1 skew
for mid-2026 kubectl (A-067).

## Proposal

Order matters: bump the advertised version first, then make CI prove it.

- **A-067:** hoist the advertised-version literals into one
  `_K8S_ADVERTISED_VERSION` constant (server_ops.py; both sites read it);
  bump to the minor that puts the pinned CI kubectl inside supported skew
  (pick against the chosen client pin — e.g. advertise 1.33.x for a
  1.34.x kubectl; finalize at implementation from the current stable
  matrix). README records the tested client versions.
- **A-022:** a **step inside the existing full-lane test job** (not a new
  aggregate-feeding job — avoids rewiring `CI Result` needs and its
  contract anchors):
  - install kubectl + Helm with exact version pins AND sha256
    verification (curl from the official dl endpoints; checksums inline
    in the workflow — the same reproducibility posture the workflow-pip
    lint enforces for pip),
  - export `AMC_RUN_REAL_CLIENT_SMOKE=1`,
  - run the two smokes serially (`pytest -n 0 -k <smoke selector>`),
  - step only in the full lane (quick/lightweight lanes skip it), and
    the version pins live next to a comment naming this task + the bump
    checklist (dependency-hygiene A-044's pattern: pins need a documented
    update path — add these two to the same "Pinned tools bump" list).

## Boundaries And Non-Goals

- No new smoke scenarios — the two existing env-gated tests are the
  scope; broadening real-client coverage is future work driven by the
  unsupported-trace backlog.
- No aggregate/branch-protection changes.
- No kind/minikube — the smokes talk to `amc serve`'s facade, no real
  cluster involved.

## Affected Files

`src/anomaly_metric_creator/server_ops.py` (constant + bump),
`.github/workflows/ci.yml` (full-lane step),
`tests/test_server.py` (only if the smokes need version-string
assertions updated), README (tested versions),
docs/DEVELOPMENT_CYCLE.md ("Pinned tools bump" additions),
`.trellis/audit/ledger.md` flips (A-022, A-067).

## Risks And Edge Cases

- kubectl's skew warning is stderr-only and version-dependent — the
  smoke's assertion should check functional output, and the "warning
  gone" acceptance is verified by grepping the smoke's captured stderr in
  CI logs, not by a brittle assertion (decide in-PR; prefer asserting
  absence of `version difference` on the pinned pair since both sides
  are pinned).
- Binary downloads add network flake surface: retry the curl (3×) and
  fail the step with a clear message; checksum mismatch must hard-fail
  (supply-chain posture).
- The version bump may change Table/object fields real clients request —
  run the smokes locally against the bump BEFORE pushing (that is the
  point of the ordering).

## Validation

- Local: pinned kubectl/helm in a scratch dir, `AMC_RUN_REAL_CLIENT_SMOKE=1
  pytest -n 0 -k smoke` green against the bumped facade.
- CI: full-lane run shows the step green; quick lane shows it absent.
- Grep: no remaining `1.29.4` literals (`rg '1\.29\.4' src/`).
