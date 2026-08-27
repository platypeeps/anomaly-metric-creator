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

## Scope As Landed

Review widened this beyond generate-key validation, and the task contract
should say so rather than leave the PRD describing a smaller change than
shipped. Everything below was found by reviewing this code, and all of it
predates the task:

- `--config` refusals other than the generate arm did not name the file.
- `server` keys were name-checked but their values never were.
- Config values were emitted as separate argv tokens, so a value starting
  with `-` was read as an option (`namespace: "-weird"` failed).
- Parser diagnostics echoed config values back, including secrets. Masking
  them failed four review rounds running -- argparse echoes a value in more
  forms than it was written in, whitespace and newlines defeat a per-line
  pass, and YAML and JSON parse errors quote the file's own text. Landed
  instead as a structural rule: no config error carries anything derived
  from a config value, only the file, the section, and the flag names.
- A `generate` key naming a serve flag silently configured the server, and
  one arriving bare (`host: true`) died in the combined parse naming no file.
- A YAML non-string key escaped as `AttributeError` past the refusal, and a
  YAML constructor error (`port: !!int "abc"`) escaped as a bare `ValueError`.
- Two config keys differing only in `_` vs `-` both became the same flag, so
  one setting vanished silently.
- `_strip_serve_config_arg` scanned past `--`.

The acceptance criteria below cover the original scope; these are recorded
so the difference is deliberate and reviewable, not silent.

Found and **not** taken: a mistyped `--conf` is unrecognized by the serve
parser, so the config is silently ignored and its `auth_token` never applied.
That is serve-flag typo handling in general -- `--por 9999` fails the same
way -- rather than `--config` validation, and a guard for this one flag would
be arbitrary. Left outstanding for its own task.

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

Original scope:

- [x] An unknown `generate` key raises a clear `ValueError` naming the key and
      the config path at load time — before generation runs.
      (`test_unknown_generate_config_key_is_rejected_naming_the_file`)
- [x] Every currently-valid generate key still loads (parametrized test derived
      from the real parser surface, with a non-empty guard so the test can't go
      vacuously green). (`test_valid_generate_config_keys_survive_the_probe`,
      guarded by `test_valid_generate_config_key_sample_is_not_empty`)
- [x] The precedence test (CLI flag overrides config value) still passes.
      (`test_serve_cli_flags_override_config_file_values`)
- [x] The serve `--config` description in
      `.trellis/spec/amc/backend/api-cli-server.md` § Serve Mode notes the
      symmetric validation.

Scope As Landed (each item above in that section, with its test):

- [x] Every `--config` refusal names the file.
      (`test_every_config_load_refusal_names_the_file`)
- [x] `server` values are validated, not just their key names.
      (`test_a_bad_server_config_value_is_rejected_naming_the_file`)
- [x] A value starting with `-` is not read as an option.
      (`test_a_config_value_starting_with_a_dash_is_not_read_as_an_option`)
- [x] No config error carries anything derived from a config value, across all
      five leak shapes. (`test_no_config_refusal_ever_prints_a_config_value`,
      `test_a_yaml_error_reports_a_position_not_the_files_own_words`)
- [x] A `generate` key naming a serve flag is refused, whether it carries a
      value or arrives bare. (`test_a_generate_key_naming_a_serve_flag_is_refused`,
      `test_a_serve_flag_arriving_bare_from_generate_is_attributed`)
- [x] Non-string and constructor-failing YAML keys/values are refused, not
      crashed on. (`test_a_non_string_config_key_is_refused_not_crashed_on`,
      `test_a_yaml_constructor_error_still_names_the_file`)
- [x] Two keys normalizing to one flag are refused rather than one silently
      winning. (`test_two_keys_naming_the_same_flag_are_refused`)
- [x] `_strip_serve_config_arg` stops at `--`.
      (`test_config_stripping_stops_at_the_end_of_options_marker`)
- [x] `null`/`false` keys are vouched against the real parser, so a typo is
      refused and `otel_verbose: false` still loads.
      (`test_unvouchable_no_flag_generate_keys_are_loud_not_silently_dropped`,
      `test_a_real_switch_may_still_be_turned_off_by_a_no_flag_value`)
- [x] The `--config` cluster moved to the `server_config.py` leaf, leaving
      `server.py` below its pre-task size (2096 → 1978).
      (`tools/check_module_size.py`, `test_the_config_cluster_is_patched_at_its_own_module`)

## Notes

- Deriving the allowlist from `parse_args` (not a hand-copied set) is the
  important design choice — it prevents the two from drifting when a new flag is
  added.
