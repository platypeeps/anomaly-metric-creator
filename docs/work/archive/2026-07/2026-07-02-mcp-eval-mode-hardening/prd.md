---
title: "MCP eval mode: ground-truth wall, auth interaction, docs"
status: done
created: 2026-07-02
branch: feat/mcp-eval-mode
---
# MCP eval mode: ground-truth wall, auth interaction, docs

## Context

- **Parent:** `07-02-mcp-server-facade` (shared design decisions binding).
- **Depends on:** `07-02-mcp-facade-core`, `07-02-mcp-analysis-tools`,
  `07-02-mcp-ops-tools-and-tracing` (this task hardens and documents what
  they built).
- **Threat model:** the "adversary" is the agent under test. If it can read
  the anomaly manifest, the scenario catalog, or ground-truth descriptions
  through any endpoint it can reach, every eval score is invalid. In
  mock-mcp-service this wall is structural (ground truth lives only in the
  scenario YAML the server never serves); in AMC the ground truth is
  *served today* by `/v1/anomalies` and `/v1/scenarios` on the same
  listener as `/mcp`.

## Goal

A documented, tested eval deployment mode where the agent-reachable surface
provably excludes ground truth, and the eval-loop workflow (agent on
`/mcp`, harness holding `anomalies.csv`) is written up for users.

## Requirements

### Ground-truth wall (structural, not conventional)

- Add an `--mcp-eval-mode` server flag (name final at implementation). When
  set:
  - Ground-truth-bearing app endpoints return 404 (or an explicit
    403-with-reason; pick one and document it): `/v1/anomalies`,
    `/v1/scenarios`, `/v1/debug/*`, the debug UI shell, and
    `/v1/logs/stream`'s generation-status metadata if it names scenarios.
  - `/v1/state` is reduced to a shape that does not name active scenarios
    or anomaly counts (or is blocked outright — decide and document).
  - The kubectl/Helm and `/v1/commands` surfaces stay available (they are
    part of the investigation surface) — but see the audit sweep item
    below.
- The deny-list must be derived from one registry of "rubric-bearing
  endpoints" adjacent to the route dispatch — not scattered `if` checks —
  so a future endpoint addition has exactly one place to classify itself.
- Generated-artifact hygiene: in eval mode the server must not serve
  `anomalies.csv` or `schema.json`'s scenario metadata through any file
  path (verify no static-file route exposes `output_dir`).
- **Audit sweep (one-time, part of this task):** walk every MCP tool
  description and response builder plus every operator-visible ops string
  for rubric leakage — scenario slugs, `anomalies.csv` description text,
  "anomaly"-labeled fields, or magnitude give-aways that name the planted
  fault. Fix or reword findings in this task.

### Auth / limits interaction

- When `--auth-token` is set, `POST /mcp` requires `Authorization: Bearer`
  like other app endpoints; the failure is a JSON-RPC-shaped error.
  `/healthz` / `/readyz` stay open.
- `--rate-limit-per-minute` covers MCP calls (they are command-like); the
  429 is JSON-RPC-shaped. `--max-request-body-bytes` behavior confirmed
  (already required in facade-core; asserted again here as part of the
  hardening test set).
- Non-loopback bind rules apply unchanged: `/mcp` on a non-loopback host
  requires `--auth-token` unless `--allow-remote-without-auth`.
- Structured request logging (`--structured-log`) records MCP requests with
  the same query/body secret redaction as other endpoints.

### Documentation

- README: new "Evaluating agents against AMC" section — starting the
  server in eval mode, pointing an MCP client at `/mcp`, which artifacts
  the harness keeps (`anomalies.csv`, scenario descriptions), a scoring
  sketch, and the explicit warning that non-eval mode serves ground truth.
- CLAUDE.md: document `server_mcp.py`'s place in the server module layout,
  the rubric-endpoint registry, and the ground-truth wall as a contract
  (any new tool/endpoint must classify itself).
- `--help` text for the new flag states plainly what it hides and why.

## Acceptance Criteria

- [x] In eval mode, every rubric-bearing endpoint returns the documented
      refusal — parametrized test over the registry, plus a
      registry-completeness test that fails when a route handler is added
      without a classification.
- [x] In eval mode, a full serialized `tools/list` + every tool's response
      on a default run contains no scenario slug from `SCENARIOS` and no
      `description` string from `anomalies.csv` (automated grep-negative
      sweep test, not a manual checklist).
- [x] Auth, rate-limit, and body-cap behaviors on `/mcp` each covered by a
      focused test (401-equivalent, 429-equivalent, over-limit — all
      JSON-RPC-shaped).
- [x] Default (non-eval) mode is byte-for-byte unchanged for all existing
      endpoints; existing suite passes.
- [x] README and CLAUDE.md sections landed in the same diff (doc-sync
      checklist heading).

## Notes

- Prefer 404 over 403 for hidden endpoints if fingerprint-resistance
  matters less than simplicity; either way the choice must be uniform and
  stated in CLAUDE.md.
- The grep-negative sweep doubles as the regression guard for future
  scenario additions: a new scenario whose slug happens to appear in an ops
  string will trip it at test time.
