# Interactive failure-mode launcher — Design (SD Work Designs, 2026-07-17)

## Overview

The PRD leaves the surface choice open (subcommand vs script vs serve
convenience). Verified state: `amc serve --scenarios <slug> --signal-level
<lvl>` already launches exactly the environment the PRD describes (serve
forwards generation flags to `parse_args`; unknown slugs already exit
non-zero naming the slug and the catalog — pinned by
`tests/test_scenarios.py` validation tests). The startup banner
(server.py:1551–1562) prints only three URLs + auth lines; there is no
kubectl/Helm/reset guidance and no README recipe for the workflow.

## Proposal

**Decision: no new subcommand, no wrapper script.** The launcher *is*
`amc serve --scenarios <slug>`; the task closes the two real gaps —
startup-output affordances and documentation:

1. **Inspection banner block.** After the existing three URL lines,
   `serve_main` prints a copyable block:
   - kubeconfig fetch: `curl -fsS [-H "Authorization: Bearer …"]
     http://<host>:<port>/v1/kubeconfig -o amc-kubeconfig` +
     `export KUBECONFIG=$PWD/amc-kubeconfig`
   - examples: `kubectl get pods -n <namespace>`, `kubectl get events -n
     <namespace>`, `helm list -n <namespace>` (namespace from
     `serve_args.namespace`)
   - reset hint: `curl -X POST http://<host>:<port>/v1/mutations/reset`
     (ties into `06-29-quick-simulator-environment-reset`, which owns the
     reset contract/docs)
   - an `Active scenarios: <slugs or "none">` line — **printed only when
     not in eval mode**; operator stdout is not an agent-reachable surface,
     but suppressing it under `--mcp-eval-mode` keeps every scenario-slug
     emission behind one uniform rule and costs nothing.
   Auth-aware: when `security.auth_token` is set, curl examples carry the
   `-H "Authorization: Bearer <token>"` header (the kubeconfig already
   embeds it).
2. **Docs.** README gains a "Launch a failure-mode environment" section:
   the one-command recipe, the failure-mode → scenario-slug mapping
   sentence (point at the existing scenario-catalog table + `--signal-level`
   semantics), the banner block explained, and a teardown note (Ctrl-C).
3. **Tests.** Extend/reuse the `serve_main` stub-server pattern from
   `07-17-audit-serve-main-wiring-tests` (land that first; its stub +
   capsys machinery is exactly what banner assertions need): assert the
   banner contains the kubeconfig fetch line, a kubectl example with the
   right namespace, the reset hint, the auth header iff token set, and no
   scenario slugs under `--mcp-eval-mode`. Plus one test that an unknown
   `--scenarios` slug via `serve` argv exits non-zero mentioning the
   catalog (pins the acceptance bullet on the serve path specifically).

## Boundaries And Non-Goals

- No duplicate scenario selector, no new subcommand, no shell wrapper.
- No change to serve security defaults (banner is print-only).
- Reset *behavior* belongs to the reset task; this task only prints the
  hint line.

## Affected Files

- `src/anomaly_metric_creator/server.py` (banner block in `serve_main`),
- `README.md`, `tests/` (banner assertions; file shared with or adjacent
  to `test_serve_main_wiring.py`),
- `.trellis/spec/amc/backend/api-cli-server.md` if it documents serve
  startup output.

## Risks And Edge Cases

- Banner content must not break `test_server.py` assertions that grep
  serve stdout (check before adding lines).
- Host formatting: `httpd.server_address` returns the bind host —
  examples should render `127.0.0.1` when bound to `0.0.0.0`? No:
  print the literal bind host; remote-bind users know their address.
  Keep it simple and note it in the README.
- Token in stdout: the banner already implies the operator holds the
  token; printing it in a copyable curl line is acceptable for a local
  workshop tool but gate it — print `Bearer <token>` only when the bind
  is loopback; on non-loopback binds print `-H "Authorization: Bearer
  $AMC_TOKEN"` placeholder instead (avoids tokens in remote logs).

## Validation

- `pytest tests/test_serve_main_wiring.py tests/test_server.py -n 0`,
  then full suite.
- Manual: `amc serve --scenarios db_stall --signal-level high`, walk the
  printed commands end-to-end once with real kubectl + helm.
