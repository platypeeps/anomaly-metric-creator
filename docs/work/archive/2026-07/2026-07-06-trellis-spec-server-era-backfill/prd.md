---
title: Backfill Trellis specs for the server era
status: done
created: 2026-07-06
---
# Backfill Trellis specs for the server era

## Review context

- **Source:** deep-dive documentation review, 2026-07-06.
- **Confidence:** CONFIRMED (grep across all 12 spec files: zero hits for
  MCP, eval mode, the remote-bind bounds, or atomic publication).
- **Severity:** HIGH for governance — the directory the repo declares
  canonical does not cover the server era, so the "update the focused
  Trellis spec first" rule misdirects agents away from the only complete
  source (CLAUDE.md).
- **Category:** documentation / spec governance.

## Goal

Backfill `.trellis/spec/amc/backend/` so it actually covers the MCP
facade, eval mode, the remote-bind resource bounds, and the atomic
publication contract — restoring the credibility of the spec-first rule.

## Problem (verified 2026-07-06)

- `architecture.md` names only 3 of the 10 extracted modules
  (schema_impl, validate_impl, otel_stream) and omits `server_mcp.py`
  from the server-module list — 7 extraction PRs (#178/#181/#183/#185)
  never touched it.
- `api-cli-server.md` (whose spec-map slot is "serve mode,
  HTTP/Kubernetes/Helm API"): no MCP endpoint contract, no eval mode; its
  output-cleanup contract describes pre-atomic semantics (PR #170 changed
  them), and it omits the `--emit gauges` → `metrics` dependency.
- `operations-security-logging.md` (domain: auth/CORS/limits): no
  `--max-concurrent-requests` / `--max-sse-connections` /
  `--socket-timeout-seconds` (PR #188, defaults-on bounds).
- Spec-first WAS honored for the two most recent changes (schema/validate
  + otel-stream extractions; the otel request-cap fix updated only the
  spec) — the gap is the 2026-07-02/03 server-era feature family.

## Requirements

- `architecture.md`: full extraction ledger (all ten modules + facades)
  and the server module map including `server_mcp.py`; keep it a map,
  not a CLAUDE.md clone.
- `api-cli-server.md` (or a new `mcp.md` if the section outgrows it): MCP
  endpoint contract (JSON-RPC subset, `MCP_TOOLS` registry rule,
  ground-truth wall, eval-mode wall — including the extended
  no-slug-on-any-surface rule once
  `07-06-eval-mode-ground-truth-wall-completeness` lands), the atomic
  publication contract, and the `--emit gauges` → `metrics` dependency.
- `operations-security-logging.md`: the three remote-bind bounds with
  defaults and the disable-with-0 convention; the rate-limiter idle-bucket
  sweep.
- Cross-check every backfilled statement against code at write time (the
  spec inherits the doc-drift risk this repo's checklist warns about).

## Acceptance Criteria

- [x] `grep -ri "mcp|eval.mode|atomic|max-sse"` over the spec dir hits the
      right documents with accurate content.
- [x] `architecture.md` module list equals `src/anomaly_metric_creator/`
      reality on the day it merges.
- [x] No contradiction between backfilled spec text and CLAUDE.md
      (spot-check the overlapping sections).

## Resolution (2026-07-07)

Backfilled the three spec files, every claim cross-checked against code:

- `architecture.md`: the Module Boundaries section now names all ten
  extracted modules (was 3) and adds `server_mcp.py` (the MCP facade) to the
  server-module list with its role and test sources.
- `api-cli-server.md`: new "MCP Facade and Eval Mode" section (JSON-RPC
  contract, `MCP_TOOLS` registry, ground-truth wall, and the full eval-mode
  wall including the `#209` no-slug-on-any-surface rule + the
  rubric-404-before-auth-per-method ordering); the atomic-publication
  contract added to Output Contracts; the `--emit gauges` → `metrics`
  dependency corrected (was only `combined`).
- `operations-security-logging.md`: the three remote-bind bounds
  (`--max-concurrent-requests` 64, `--max-sse-connections` 16,
  `--socket-timeout-seconds` 30, each disablable with 0) + the rate-limiter
  idle-bucket sweep, with a `SECURITY.md` cross-link for the remote-bind
  posture.

Verified: the bounds default constants (64/16/30.0), the eval registry
(`_RUBRIC_ENDPOINT_*`/`_rubric_endpoint`), the atomic helpers
(`_atomic_artifact_open`/`_atomic_write_text`/`_known_artifact_filenames`),
`MCP_TOOLS`, and `test_every_dispatched_route_is_classified` all exist as
cited. `check_copilot_instruction_contract.py` and the role-name lint pass;
the acceptance grep hits all three docs.

## Notes

- After this lands, the per-feature CLAUDE.md sections (MCP, serve,
  hardening) become candidates to shrink to pointers — CLAUDE.md diet is
  tracked informally in `07-06-docs-refresh-sweep`'s notes, not here.
