# Security Policy

## Intended use and trust model

`anomaly-metric-creator` is a **synthetic data generator and local lab
tool**. Its default and supported posture is:

- Run the CLI generator (`amc`, `python anomaly-metric-creator.py`) locally
  to write synthetic CSV/JSON artifacts.
- Run `amc serve` bound to **loopback** (`127.0.0.1`/`::1`) for interactive
  workshops and AI incident-response evaluations, reachable only from the
  same machine.

The core assumption is **no untrusted users reach the server**. Every human
or client that can open a connection is trusted to the same degree as
someone with a shell on the host. The tool ships a real-`kubectl`/Helm-
compatible API and an in-process command simulator; all of it is synthetic,
but it is not written to withstand a hostile client on the same network
segment.

### Remote-bind posture (the load-bearing decision)

**A non-loopback bind is discouraged and only tolerated for isolated lab
networks. It is not a supported production posture.** The server has no
TLS, no user model beyond a single shared bearer token, and no audit
logging suitable for a multi-tenant deployment. If you must expose it
beyond loopback:

- Restrict it to a trusted LAN or an SSH tunnel; never the public internet.
- Front it with a reverse proxy that terminates TLS.
- Always pass `--auth-token`.

To bind outside loopback the server **requires** either `--auth-token` or
the explicit `--allow-remote-without-auth` escape hatch; otherwise startup
fails
([server.py:1495](src/anomaly_metric_creator/server.py:1495)). A
non-loopback bind without a token also prints a startup warning
([server.py:1561](src/anomaly_metric_creator/server.py:1561)).

## What authentication does and does not protect

When `--auth-token` is set, every endpoint requires
`Authorization: Bearer <token>` (compared in constant time via
`hmac.compare_digest`,
[server.py:804](src/anomaly_metric_creator/server.py:804)) **except** these
intentionally unauthenticated surfaces:

- `GET /healthz` and `GET /readyz` — liveness/readiness probes.
- `GET /` and `GET /debug` — the static debug-console HTML shell (it carries
  no data; its own JavaScript must then present the bearer token on every
  data request).

Everything else — the command API, the Kubernetes/Helm REST facade, the MCP
endpoint, the debug data endpoints, and the SSE streams — is refused with
`401` when the token is missing or wrong.

When no `--auth-token` is configured (the loopback-workshop default), there
is **no** authentication at all: any local client has full access. This is
acceptable only because the trust model assumes no untrusted local users.

### Evaluation mode

`amc serve --mcp-eval-mode` is a stricter posture for AI-agent evaluations:
the run's active scenarios and anomaly manifest are the scoring rubric, so
eval mode hides every rubric-bearing surface (`/v1/anomalies`,
`/v1/scenarios`, `/v1/state`, `/v1/logs/stream`, the `/v1/debug/*` prefix,
and the console shell) behind a `404` applied **before** authentication for
every HTTP method (fingerprint-resistant), and withholds active-scenario
identifiers from every investigation-open surface (ConfigMap data,
`kubectl exec … env`, `helm get values`, the Helm release payload, pod
`scenario_ids`, and the `/v1/commands` trace echo). Eval mode is a
containment boundary for a *cooperative* evaluation harness, not a defense
against an adversary with host access. Because the `/v1/debug` command-trace
export is one of those hidden surfaces, the sanctioned harness-side path to
the agent's command history in eval mode is on-disk persistence
(`--persist-command-db` / `--persist-command-log`), read offline with
`amc trace-bundle`; without it the traces are unrecoverable after shutdown and
`serve` prints a startup warning.

## Credential handling

- **Bearer token in kubeconfig.** `GET /v1/kubeconfig` embeds the configured
  bearer token so stock `kubectl`/Helm clients can authenticate
  ([server.py:592](src/anomaly_metric_creator/server.py:592)). Anyone who
  can fetch the kubeconfig obtains the token; the endpoint is itself bearer-
  gated when a token is set.
- **Redaction in traces and logs.** Command/API traces, the structured
  request log, and the debug UI redact bearer tokens, token-like query
  parameters, passwords, secrets, and client-key-shaped values before they
  reach memory, JSONL, SQLite, or the browser.
- **`otel-activity.log`.** OTEL transport diagnostics mask sensitive HTTP
  headers before any value reaches the on-disk log, using **two deliberately
  different postures** for the two trust origins
  ([redaction.py](src/anomaly_metric_creator/redaction.py)):
  - *Request side* (`_masked_headers`, headers this process builds) stays an
    **allowlist-of-sensitive** (`_SENSITIVE_HEADER_NAMES`: `Authorization`,
    `Cookie`, `Set-Cookie`, `Proxy-Authorization`, `X-Api-Key`) — we control
    the outbound set and only ever attach `Authorization`, so operational
    headers like `Content-Type` stay legible.
  - *Response side* (`_redact_sensitive_headers`, headers an untrusted
    upstream echoes back on a 4xx/5xx) is the inverse **mask-unless-known-safe**
    posture: every value is masked except a short allowlist of known-safe
    operational headers (`_SAFE_RESPONSE_HEADER_NAMES`), so a credential
    echoed under a novel name (`X-Amz-Security-Token`, `X-Vault-Token`, …)
    defaults to masked.

  Both paths share `_mask_sensitive_value`, so a schemed `Bearer`/`Basic`
  prefix is preserved while only the credential is replaced with `***`.

## Response headers

`amc serve` sends `x-content-type-options: nosniff`,
`x-frame-options: DENY`, `referrer-policy: no-referrer`, and
`cache-control: no-store` on responses
([server.py:911](src/anomaly_metric_creator/server.py:911)). It does **not**
send `Content-Security-Policy` or `Strict-Transport-Security` — see Known
limits.

## Known limits

**Existing protections** (do not mistake these for gaps):

- **DoS bounds for reachable binds** (defaults-on, each disablable with `0`):
  a bounded worker-thread pool (`--max-concurrent-requests`, default 64), an
  SSE-connection cap (`--max-sse-connections`, default 16), a per-request
  socket timeout (`--socket-timeout-seconds`, default 30), and a rate limiter
  whose idle per-client buckets are swept each window so the limiter itself
  cannot grow unbounded.
- **Request-body caps** (`--max-request-body-bytes`) return `413` before the
  body is parsed.
- **Atomic artifact publication.** Generated files are staged and
  `os.replace`d into place, so a concurrent reader never observes a partial
  file.

**Remaining limits** (accepted for the lab-tool posture):

- **No TLS.** Traffic is plain HTTP; a remote bind must be fronted by a
  reverse proxy that terminates TLS.
- **No Content-Security-Policy** on the token-bearing debug shell, which is a
  single large inline script. Output is consistently HTML-escaped, so this is
  defense-in-depth only; a CSP would further harden the shell.
- **Single shared secret.** There is one bearer token, no per-user identity,
  and no token rotation or expiry.

## Reporting a vulnerability

Please report suspected vulnerabilities **privately**, not through a public
issue. Use GitHub's private vulnerability reporting on this repository
(the **Security** tab → **Report a vulnerability**). Include the affected
version/commit, a description, and reproduction steps. Because this is a
synthetic lab tool with the trust model above, findings that depend on an
untrusted client reaching a non-loopback bind will be assessed against that
stated posture rather than treated as production-severity by default.
