# Validate --config generate keys symmetric with server keys

## Audit context

- **Source:** first-time staff-engineer audit, 2026-07-02.
- **Confidence:** CONFIRMED (read end to end).
- **Severity:** LOW — usability / fail-loud gap, not a security issue.
- **Category:** input validation / robustness.
- **Systemic pattern:** "uneven validation rigor" — the same asymmetry shows up
  in the redaction task (rigorous in one place, pass-through in the sibling).

## Goal

Make a typo'd key under the `--config` file's `generate:` map fail loudly the
same way a typo under `server:` already does, instead of being silently ignored
or mismapped.

## Problem (concrete failure scenario)

`_load_serve_config` validates the
config file well: suffix check, `safe_load`, dict type-check, and an
**allowlist** for top-level (`server`/`generate`) and for `server` keys (its
`unknown_server` check). But the `generate` map is only type-checked as a dict
and then handed to `_config_mapping_to_argv` for flag conversion.
*(Cited by symbol rather than by line since 2026-08-26: these refs were
re-anchored once and drifted again within the same branch, because they
describe code this task is changing. Symbols survive the edit; line numbers
do not. The module moved too -- this cluster now lives in
`src/anomaly_metric_creator/server_config.py`, which is the second reason
not to pin prose to a location.)*

**When** a user writes `generate: { componentss: [...] }` (typo) or any key that
does not map to a real generate flag, **the mistake is not rejected at config
load** the way an unknown `server` key is — it either becomes a bogus `--flag`
that fails later in `legacy.parse_args` with a less obvious message, or (for a
key that collides with nothing) is silently dropped.

## Requirements

- Add a symmetric allowlist/validation for `generate` keys in
  `_load_serve_config`, analogous to `_SERVE_CONFIG_SERVER_KEYS`. The source of
  truth should be the set of generate flags `legacy.parse_args` accepts (derive
  it rather than hand-maintaining a second list — hand-lists drift, per the
  repo's single-source-of-truth rule).
- Raise a `ValueError` naming the offending key(s) and the file, matching the
  existing `unknown_server` message shape
  (its `unknown_server` check). *(Note
  2026-08-26: the `unknown_server` message does **not** name the file, unlike
  every other `_load_serve_config` diagnostic. The implementation names the
  file, per this bullet's own "and the file" requirement, via the shared
  `_config_error` helper.)*
- Preserve the existing precedence: explicit CLI flags still win over config
  values.
- Do not reject valid generate keys (verify against the real
  `--scenarios`/`--components`/`--emit`/`--otel-send`/… surface, including the
  advanced `--help-all` knobs).

## Acceptance criteria

- [ ] An unknown `generate` key raises a clear `ValueError` naming the key and
      the config path at load time — before generation runs.
- [ ] Every currently-valid generate key still loads (parametrized test derived
      from the real parser surface, with a non-empty guard so the test can't go
      vacuously green).
- [ ] The precedence test (CLI flag overrides config value) still passes.
- [ ] The serve `--config` description in
      `.trellis/spec/amc/backend/api-cli-server.md` § Serve Mode notes the
      symmetric validation.

## Notes

- Deriving the allowlist from `parse_args` (not a hand-copied set) is the
  important design choice — it prevents the two from drifting when a new flag is
  added.
