# kubectl port-forward lifecycle — Design (SD Work Designs, 2026-07-17)

## Overview

The PRD flags this as needing a design because lifecycle behavior can
affect security expectations. The governing fact: `POST /v1/commands` is
one-shot request/response — a real blocking port-forward session cannot
exist there, and opening actual listening sockets from the command
simulator would breach the "never shells out / no real network effects"
posture and the remote-bind hardening model.

## Proposal

**Simulated bounded lifecycle, zero real sockets:**

- `kubectl port-forward <pod|svc>/<name> [LOCAL:]REMOTE` validates the
  target against `resource_snapshot()` (NotFound on miss) and the remote
  port against the target's modeled ports (Kubernetes-shaped
  "unable to forward" on port miss).
- Success renders the authentic startup lines (`Forwarding from
  127.0.0.1:LOCAL -> REMOTE`, IPv6 twin) followed by one explicit
  simulator line: `simulator: no real tunnel is opened; use the fake
  API endpoints for live probes` — then exits 0. Honest-one-shot, the
  same posture the watch and logs tasks adopt for streaming flags.
- Trace classification: **partial** (matched rule notes the simulated
  no-tunnel semantics) so demand for richer behavior stays visible in
  the backlog — not "supported", because the real command blocks and
  proxies.
- Unsupported shapes → existing unsupported path: `--address`, UDS
  variants, multi-port lists beyond the first pair (or model multi-port
  rendering trivially — decide by output-shape cost at implementation),
  resource kinds without modeled ports.

**Security invariants (the reason for this design):** no sockets bound,
no change to `--host`/auth gates, nothing in the rendered output invites
connecting to a port the simulator does not serve (the message points at
the real fake-API base URL instead).

## Boundaries And Non-Goals

- No real tunnel/proxy mode, even behind a flag — if a workshop ever
  needs a live proxy that is a separate consented design with SECURITY.md
  review.
- No background "session" state in `SimulationMutations` (nothing to
  reset; keeps the reset task's contract untouched).

## Affected Files

`src/anomaly_metric_creator/server_ops.py` (renderer + port model
lookup + classification), `tests/test_server.py`,
`tests/test_server_ops_fuzz.py` (malformed port specs).

## Risks And Edge Cases

- Port validation source: the snapshot's service/container port fields —
  reuse whatever `_render_describe`/objects already expose; do not
  invent a port registry (single-source rule).
- `LOCAL:REMOTE` parsing edge cases (bare port, `:REMOTE`, invalid
  ints) → kubectl-shaped errors; fuzz them.

## Validation

- `pytest tests/test_server.py -n 0 -k port_forward` + fuzz; full suite.
- Security check in review: grep the diff for any `socket`/`bind` usage
  (must be none).
