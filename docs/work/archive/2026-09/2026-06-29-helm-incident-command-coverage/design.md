# Helm incident command coverage — Design (SD Work Designs, 2026-07-17)

## Overview

The PRD lists four candidate families (lint, dependency, repo, chart
metadata) with a defer-what-doesn't-map rule. Incident triage reads
*deployed state*; lint/dependency/repo are authoring-time. The family
that maps to real incident workflows is **chart metadata against the
deployed release**: "what chart/values is this release actually
running?"

## Proposal

- Implement `helm show chart <chart-ref>` and `helm show values
  <chart-ref>` plus `helm get metadata <release>` (Helm 4's
  release-metadata command), all rendered from the existing release
  state: chart name/version/appVersion from the release records the Helm
  Secret encoder already builds, values from the same layered-values
  source `helm get values` uses. Chart refs resolve only to the charts
  the simulator's releases reference; anything else → Helm-shaped
  "chart not found" + nonzero exit.
- `helm lint`, `helm dependency *`, `helm repo *` → explicit
  **unsupported** traces with Helm-shaped stderr (recording demand in
  the debug backlog per the PRD), not silent errors.
- Scenario appropriateness: metadata output is scenario-independent
  (charts don't change under incidents) except revision/status fields,
  which come from the release overlay — so mutated releases (rollbacks)
  show their overlay revision. Eval-wall: `helm get values`/release
  payloads already route scenario slugs through
  `_exposed_active_scenarios`; the new renders reuse the same accessors
  so eval mode inherits the redaction for free — assert it.

## Boundaries And Non-Goals

- No repo index emulation, no chart file trees, no `helm show all/crds`.
- No changes to install/upgrade/value layering (acceptance bullet).

## Affected Files

`src/anomaly_metric_creator/server_ops.py` (renderers + dispatch +
unsupported classifications), `tests/test_server.py`,
`tests/test_server_eval_mode.py` (values redaction assertion), README
serve command list if it enumerates Helm support.

## Risks And Edge Cases

- `helm get metadata` shape differs between Helm 3/4 — match the Helm 4
  output the real-client smokes pin (coordinate with
  `07-17-audit-real-client-smoke-ci`'s pinned binary for a live
  cross-check).
- Values rendering must be byte-stable across calls (sorted keys, same
  YAML dump settings as `helm get values`).

## Validation

- `pytest tests/test_server.py -n 0 -k helm` + eval sweep; full suite.
- One manual `helm show values`/`helm get metadata` against a live serve
  with the pinned Helm 4 binary.
