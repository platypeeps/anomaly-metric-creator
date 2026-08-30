---
title: Document the server trust boundary in SECURITY.md
status: done
created: 2026-07-02
---
# Document the server trust boundary in SECURITY.md

## Audit context

- **Source:** first-time staff-engineer audit, 2026-07-02.
- **Confidence:** CONFIRMED absent.
- **Severity:** MEDIUM — cheap, and it unblocks/scopes the other security tasks.
- **Category:** conspicuously-absent / governance.

## Goal

Write down the intended trust boundary and threat model for a tool that ships an
authenticated HTTP server capable of binding non-loopback, so that hardening
decisions are made deliberately rather than implied by code.

## Problem

There is no `SECURITY.md` and no documented threat model. The code
simultaneously:

- ships a bearer-auth'd HTTP server with a real-`kubectl`/Helm-compatible API
  and a command simulator, and
- supports non-loopback binds (with a token or `--allow-remote-without-auth`,
  gated at [server.py:1485](src/anomaly_metric_creator/server.py:1485)),

yet the README/CLAUDE.md frame it as a "local workshop" tool. Whether a
reachable remote instance is a **supported** posture or a **tolerated** one is
undefined — and that decision determines how far
`07-02-server-remote-bind-hardening` must go and whether the redaction posture
in `07-02-redaction-allowlist-hardening` is "defense in depth" or "load-bearing".

## Requirements

- Add `SECURITY.md` at the repo root covering:
  - **Intended use / trust model:** who is expected to reach the server
    (loopback-only workshop? trusted-LAN demo? never internet-exposed?), and the
    explicit "no untrusted users" assumption.
  - **What auth does and does not protect:** the static debug shell (`/`,
    `/debug`) and `/healthz`/`/readyz` are intentionally unauthenticated
    ([server.py:547](src/anomaly_metric_creator/server.py:547)–560); every data
    endpoint requires the bearer token when configured.
  - **Credential handling:** the bearer token is embedded in `/v1/kubeconfig`
    for real clients; header/query/command redaction in traces, logs, and
    `otel-activity.log`; the mask-unless-known-safe posture once
    `07-02-redaction-allowlist-hardening` lands.
  - **Known limits** (link the hardening tasks) — *updated 2026-07-06: the
    original list is partly stale.* Connection/thread caps, SSE slots,
    socket timeouts, and rate-limiter bucket sweeping **landed** in PR #188
    (`07-02-server-remote-bind-hardening`, defaults-on, disablable with 0),
    and artifact writes are now **atomic** (PR #170) — describe both as
    existing protections, not gaps. Remaining limits to document: no TLS
    (operator must front with a reverse proxy for remote), no
    Content-Security-Policy on the token-bearing debug shell
    (`_send_common_headers` sends no CSP and the shell is one large inline
    script), and the eval-mode wall caveats tracked in the 2026-07-06
    review (scenario-slug exposure via ops surfaces until that fix lands).
  - **Reporting:** how to report a vulnerability privately.
- Decide and record the **remote-bind posture** (supported vs discouraged). This
  is the key output — the hardening task's scope depends on it.
- Cross-link from `README.md` and the CLAUDE.md server-mode section.

## Acceptance criteria

- [x] `SECURITY.md` exists and states the trust boundary, the no-untrusted-users
      assumption, the credential-handling summary, known limits, and a reporting
      channel.
- [x] The remote-bind posture is explicitly stated (one sentence: supported and
      hardened, or discouraged and warned).
- [x] `README.md` and CLAUDE.md link to it.
- [x] The `role-name-leaks` lint passes on the new file
      (`tools/check_role_name_leaks.py` scans Markdown).

## Resolution (2026-07-07)

`SECURITY.md` added at the repo root. **Remote-bind posture recorded:
discouraged and only tolerated for isolated lab networks — not a supported
production posture** (matching PR #188's scoping). The doc covers the trust
model (loopback-only lab tool, no-untrusted-users assumption), what auth
does/doesn't protect (unauthenticated `/healthz`, `/readyz`, `/`, `/debug`;
everything else bearer-gated; no auth at all when no token is set), eval
mode as a cooperative-harness containment boundary (now including the
#209 slug/ordering seal), credential handling (kubeconfig token embedding,
trace/log redaction, the allowlist `otel-activity.log` masking with a
pointer to `07-02-redaction-allowlist-hardening`), the actual response
headers, and known limits split into existing protections (the PR #188
bounds, body caps, atomic writes) vs remaining gaps (no TLS, no CSP on the
debug shell, single shared secret). Reporting channel: GitHub private
vulnerability reporting. All code citations verified against current
`server.py`; role-name lint clean. README.md and CLAUDE.md link to it and
the stale "pending `07-02-security-md-and-threat-model`" reference in
CLAUDE.md was replaced.

## Notes

- ~~Do this early: it is cheap and it scopes `07-02-server-remote-bind-hardening`.~~
  *(2026-07-06: the hardening task completed first — PR #188 scoped itself as
  "lab tool, remote is not a supported posture, resource bugs fixed
  regardless". SECURITY.md should now RECORD that posture rather than gate
  the hardening.)*
- Keep the CSP/headers guidance grounded in what the server actually sends
  (`_send_common_headers`, [server.py:901](src/anomaly_metric_creator/server.py:901):
  `x-content-type-options`, `referrer-policy`, `x-frame-options`, `no-store`;
  no CSP header today).
