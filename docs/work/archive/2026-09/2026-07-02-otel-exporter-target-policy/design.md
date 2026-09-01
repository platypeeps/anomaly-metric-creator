# OTEL exporter target policy — Design (SD Work Designs, 2026-07-17)

## Overview

Decision task: the exporter POSTs wherever `--otel-endpoint` /
`MEZMO_OTEL_*` point, auth header attached, with no explicit posture.
The audit's own recommendation is **warn** (cheap; preserves exporter
flexibility; closes the stale-env-var-redirects-the-stream-with-a-token
footgun) and the PRD asks for maintainer confirmation before building —
that is the one decision gate at task start.

## Proposal (assuming "warn" is confirmed; "accept" fallback below)

1. **Always surface the resolved targets:** at stream start (both CLI
   `main()` and serve's background streamer), print one line per
   selected signal with the **resolved** endpoint (post flag-vs-env
   precedence) and whether auth is attached — the PRD's non-negotiable
   "surfaced resolved endpoint" requirement, useful under every policy.
2. **Warn condition 1 — env-sourced target:** when a signal's endpoint
   came from a `MEZMO_OTEL_*` env var rather than an explicit
   `--otel-endpoint`/`--otel-send` invocation, emit one stderr WARNING
   naming the env var and the resolved URL ("stale shell export can
   silently redirect the stream — pass --otel-endpoint to silence").
   This is the actual footgun the audit found.
3. **Warn condition 2 — remote + no token:** resolved non-loopback
   endpoint with no auth token → one informational WARNING
   (credential-free POST to a remote host).
4. Wire the resolution-origin fact where it is known:
   `_reconcile_cli_surface` seeds per-signal endpoints from env via
   `set_defaults` — thread an origin marker (env vs derived-from-base)
   alongside the endpoint values so the streamer can warn without
   re-deriving precedence. Warnings ride the structured-logging seam if
   `07-02-structured-logging-in-generator` lands first (logger.warning);
   plain stderr otherwise — coordinate, don't block.
5. **Docs:** SECURITY.md gains a short "OTEL egress" paragraph: operator
   chooses the collector; the tool surfaces resolved targets and warns
   on the two conditions; no allowlist by design (rationale: single-user
   generator tool, not a multi-tenant service — allowlist friction buys
   nothing here). CLI reference notes the warnings.

**Fallback if the maintainer picks "accept":** items 1 + 5 only (log
resolved targets + document the posture); the acceptance test asserts
the startup logging instead of warn paths.

## Boundaries And Non-Goals

- No allowlist flag (rejected with rationale recorded above — revisit
  only if the tool ever runs unattended in shared infra).
- No change to endpoint-precedence behavior (explicit base beats env;
  env supplies defaults) — regression-tested, not redesigned.

## Affected Files

`src/anomaly_metric_creator/legacy.py` (`_reconcile_cli_surface` origin
marker), `src/anomaly_metric_creator/otel_stream.py` (resolved-target
lines + warnings), SECURITY.md, README CLI reference,
`tests/test_cli.py` / `tests/test_otel_gauges.py` (warn-path +
precedence tests).

## Risks And Edge Cases

- The origin marker must not alter the argparse namespace shape any
  existing test asserts — additive dests only, seeded in
  `set_defaults`.
- Warning text goes to stderr next to existing OTEL activity notices —
  keep the `WARNING:` prefix convention and one-line shape.
- `--otel-send none` and unselected signals must produce zero warnings
  (selection is authoritative; nothing resolved → nothing to warn
  about).

## Validation

- Tests: env-sourced endpoint warns naming the var; explicit
  `--otel-endpoint` does not; remote+no-token warns; loopback+token
  silent; precedence tests unchanged; resolved-target lines asserted.
- `pytest tests/test_cli.py tests/test_otel_gauges.py -n 0` + full
  suite.
