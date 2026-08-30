---
title: Decide and enforce an OTEL exporter target policy
status: planning
created: 2026-07-02
---
# Decide and enforce an OTEL exporter target policy

## Audit context

- **Source:** first-time staff-engineer audit, 2026-07-02.
- **Confidence:** SUSPICION (design decision, not a confirmed bug).
- **Severity:** LOW — operator-configured exporter; likely acceptable as-is.
- **Category:** security posture / conscious-decision.

## Goal

Make a deliberate, documented decision about where the OTEL exporter is allowed
to send data, rather than leaving "any host the env/CLI names" as an implicit
default.

## Problem

The OTEL streaming endpoint derives from `--otel-endpoint`/`--otel-auth-token`
and the `MEZMO_OTEL_*` environment variables (per the CLAUDE.md CLI-surface
section; per-signal URLs derive as `BASE/v1/<signal>`). There is no allowlist or
egress guard: whatever host is configured is where anomaly logs/metrics/traces
(and any embedded auth header) are POSTed.

This is normal for a telemetry exporter — the operator chooses the collector.
But two things make it worth an explicit decision:

1. In `amc serve`, a config file (`--config`) or a stale shell `MEZMO_OTEL_*`
   export can silently redirect the stream; the value flows through with no
   "you are about to send to X" confirmation beyond the startup log.
2. The exporter attaches the bearer/auth header to the outbound request, so a
   misdirected endpoint receives a credential.

## Requirements

- Decide the policy and write it down (in `SECURITY.md` from
  `07-02-security-md-and-threat-model`, and/or the CLI docs). Options:
  - **Accept (document only):** state that the operator is responsible for the
    endpoint; log the resolved target clearly at startup. Lowest effort.
  - **Warn:** emit a stderr warning when the resolved endpoint is non-loopback
    and no auth token is set (credential-free POST to a remote host), or when the
    endpoint came from an env var rather than an explicit flag.
  - **Allowlist:** optional `--otel-allow-endpoint`/env allowlist that must
    match the resolved base, refusing otherwise. Highest friction; probably
    overkill for this tool.
- Whichever is chosen, ensure the **resolved** endpoint (after the
  flag-vs-env precedence rules) is surfaced to the operator, not just the raw
  input.

## Acceptance criteria

- [ ] The policy is documented (SECURITY.md and/or CLI reference) with a
      one-line rationale.
- [ ] If "warn" or "allowlist" is chosen, a focused test covers the warn/refuse
      path; if "accept" is chosen, a test asserts the resolved endpoint is
      logged at startup.
- [ ] No regression to the existing endpoint-precedence behavior (explicit base
      beats `MEZMO_OTEL_*`; env supplies the default when no base is given).

## Notes

- Recommendation from the audit: **warn**, not allowlist — cheap, preserves the
  exporter's flexibility, and closes the "stale env var silently redirects the
  stream (with a token)" footgun. Confirm with the maintainer before building.
- Lowest-priority security item; safe to defer behind the confirmed findings.
