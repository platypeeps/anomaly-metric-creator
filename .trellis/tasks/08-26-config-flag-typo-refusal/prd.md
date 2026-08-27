# Refuse a mistyped --config instead of silently ignoring the file

## Goal

`amc serve --conf f.json` starts a server with none of the file's settings
applied, and says nothing.

The serve parser sets `allow_abbrev=False`, so `--conf` is not an abbreviation
of `--config` — it is an unrecognized flag. `_extract_serve_config_path` never
sees a config path, `_load_serve_config` never runs, and the two leftover
tokens fall through into `generate_argv`, where they are the generation
parser's problem rather than an error anyone attributes to the typo.

The security-relevant case: a config supplying `auth_token` is silently not
applied. On the default bind this reaches startup — `--host` defaults to
`127.0.0.1`, and the refusal at `server.py:1579` fires only for a non-loopback
bind without `--allow-remote-without-auth`, so an empty `auth_token` is a
normal, unremarkable local start. The operator gets the server they asked for
minus the authentication they configured, with nothing said.

The scope of that claim is exactly the loopback default, and is worth stating
precisely because the remote case is *not* a silent downgrade: a config
carrying both `host` and `auth_token`, mistyped as `--conf`, loses the host
too, so it never reaches the remote guard to be refused by it. What the typo
produces is a locally-bound unauthenticated server, not a remotely-exposed
one.

Verified on `main` at the time of filing:

```
>>> ns, gen = server._parse_serve_args(["--conf", str(cfg)], parser)
auth_token: ''   port: 8088   leftover generate_argv: ['--conf', '/tmp/c.json']
```

The same config passed as `--config` yields `port: 9999` and the token set.

## Why this was not fixed in 07-02

`07-02-config-generate-key-validation` found this while hardening `--config`
and deliberately left it. `--por 9999` fails the same way: the serve parser
does not own it, so it becomes a generate token and the port silently stays at
its default. A guard written for `--config` alone would fix the one flag the
task happened to be looking at and leave the shape intact everywhere else.
That is why this is its own task — the decision to make is about serve-level
flag typos in general, not about `--config`.

## The tension to resolve

Unknown tokens **must** pass through to generation: that is how `amc serve`
forwards generate flags, and it is load-bearing. So "refuse what the serve
parser does not recognize" is not available as a rule.

What makes a principled rule reachable is that `_parse_serve_args` already
resolves the generate parser — `_resolve_generate_parse_args` — for the config
probes. Both parsers are therefore in hand at the same point, which was not
true before 07-02. A token owned by *neither* is a token nothing will ever
act on.

Candidate rules, to be decided rather than assumed:

1. **Refuse a token neither parser owns.** Principled and catches `--conf`,
   `--por`, and every future typo alike. Cost: it makes serve's pass-through
   strict, and anything that relied on an unowned token reaching generation
   and failing *there* now fails earlier with a different message.
2. **Refuse a token that is a near-miss of a serve flag** (prefix, or small
   edit distance). Narrower, but the threshold is a judgment call and
   `--con` is a genuine prefix of both `--config` and
   `--continuous-generate`.
3. **Warn rather than refuse.** Preserves every current behavior; relies on
   the operator reading stderr, which is exactly what failed here.

## Requirements

- Decide the rule and record the rationale before implementing. The decision
  is the deliverable; the code is small either way.
- Whatever is chosen must cover `--por 9999` as well as `--conf`, or must say
  explicitly why it does not.
- Legitimate generate flags forwarded through `amc serve` must keep working
  unchanged. Derive that set from the real generate parser — never a second
  hand-maintained list, per the repo's one-registry-per-fact rule.
- A refusal must name the **flag** and say what to do, matching the
  attribution posture `--config` errors now hold. The flag is the portion
  before the first `=`, never the whole token: argparse accepts
  `--auth-token=s3cret` as a single argv element, so echoing "the offending
  token" is how the value leaks. `server_config._config_flag_names` already
  does exactly this split and is the function to reuse.
- Do not print any flag's *value*, whether it arrived as a separate argv
  element or after an `=`. `--auth-token` is a serve flag, and a typo'd key is
  on no allowlist. The no-config-values rule established in 07-02 applies to
  this surface too.

## Acceptance Criteria

- [ ] The chosen rule is recorded with rationale, including why the rejected
      candidates were rejected.
- [ ] `amc serve --conf f.json` no longer starts a server with the file
      unapplied, covered by a test asserting the refusal names the token.
- [ ] `amc serve --por 9999` is handled per the recorded decision, covered by
      a test — whether that is a refusal or an explicitly-reasoned exemption.
- [ ] A legitimate generate flag forwarded through `amc serve` still reaches
      generation, covered by a test derived from the real generate parser
      rather than a hard-coded flag name.
- [ ] No refusal message contains a value taken from the command line.
- [ ] `README.md` and `api-cli-server.md` § Serve Mode describe the behavior.

## Notes

- Source: `07-02-config-generate-key-validation`, recorded in its `prd.md`
  § Scope As Landed ("Found and **not** taken") and in its `design.md`
  non-goals. Both name this task as the intended home.
- The eval-mode ground-truth wall is not implicated: this changes startup
  argument handling, not any endpoint an eval agent can read.
