# Symmetric --config generate-key validation — Design (SD Work Designs, 2026-07-17)

## Overview

`_load_serve_config` allowlists `server` keys (its `unknown_server`
check) but only dict-type-checks the `generate` map before
`_config_mapping_to_argv` converts it to flags — a typo'd generate key
either fails later in `parse_args` with a message that never names the
config file, or is silently dropped. The PRD's design constraint: derive
the validation from the real parser surface, never a hand-list. A prior
session (2026-07-08) verified the blocking fact: `parse_args` builds its
parser inline across ~700 lines — extracting a `build_parser()` for
introspection is impractical.

## Proposal

**Validation-by-dry-run-parse — the real parser IS the allowlist.**
No parser extraction, no hand-list, zero drift:

1. *(Placement corrected 2026-08-26.)* The probe cannot live in
   `_load_serve_config`: that function returns two dicts and never calls
   `_config_mapping_to_argv` — the conversion happens one frame up, in
   `_parse_serve_args`. The probe therefore runs in
   `_parse_serve_args`, inside the same `try` whose `except ValueError`
   already routes config errors to `parser.error`. The `ValueError` stays
   observable at a unit seam because the probe is its own function,
   `_probe_config_generate_argv`; the CLI path still
   surfaces it as `SystemExit(2)`. Run a **probe parse**: call `legacy.parse_args(argv)`
   inside a trap that captures `SystemExit` + intercepted stderr.
   On failure, raise `ValueError` shaped like the existing
   `unknown_server` message — naming the config path and embedding the
   parser's own diagnostic (which names the bogus `--componentss` flag,
   giving per-key attribution). The probe is exactly the parse
   `serve_main` would run later, moved earlier with file attribution —
   no behavior it could reject survives today anyway.
2. Audit `_config_mapping_to_argv` for silent-drop
   arms (the PRD's "collides with nothing" case): any key shape it
   cannot convert to a flag must be checked, not skipped.
   *(Resolved 2026-08-26 as a vouch rather than a refusal, because `null`
   and `false` are both real ways to write "leave this switch alone":
   `_vouch_no_flag_generate_keys` asks the parser whether the bare flag
   is a switch, so a typo is refused and `otel_verbose: false` still
   loads. `_config_mapping_to_argv` stays a pure conversion.)*
3. Precedence untouched: explicit CLI flags are appended after config
   flags and still win.
   *(Revised 2026-08-26. The probe was to validate the config-derived
   argv alone, so a config value the CLI overrides must still be valid.
   Review found that refuses working configurations: several generate
   gates are cross-flag, so `interval_seconds: 1` overflows the preflight
   cell cap against the defaults while the real narrowed run is fine. The
   probe now decides on the merged argv and reports the config's own
   diagnostic; the cost is that an overridden config value is no longer
   separately validated, since the run never uses it.)*

Probe-safety audit (record results in the PR): parsing twice must be
safe. `parse_args` is **not** side-effect-free — see the audit result
below for what it touches and why the double parse is safe anyway. Its
validation gates include file-existence checks (`--instance-config`),
which is correct probe behavior (the later real parse would fail
identically; earlier + attributed is the improvement).

*(Audit completed 2026-08-26. The pre-audit draft of the paragraph above
claimed `parse_args` was side-effect-free apart from `set_defaults` env
reads; it is not, and that claim has been corrected in place rather than
left standing beside its own refutation.* `parse_args` opens with `_refresh_cli_runtime(runtime_key)` (`cli_args.py:292`), which
mutates module globals — `COMPONENTS`, `SCENARIOS`,
`DEFAULT_METRICS_PER_COMPONENT`. That is safe for a double parse because
the call is idempotent: it re-reads the live registries through the
runtime getters and overwrites the globals with the same values rather
than accumulating (`cli_args.py:62-71`). The conclusion holds, but on
different grounds than stated.*)

## Boundaries And Non-Goals

- No parse_args refactor (decomp step 8 owns that move; this design
  works identically before and after it — the probe calls the same
  public entrypoint).
- Scope grew during review, and deliberately: three fixes here are not
  about generate-key validation as such but were found by reviewing this
  code and would have been dishonest to leave (`_strip_serve_config_arg`
  scanning past `--`, config values emitted as separate argv tokens so a
  leading `-` was read as an option, and parser diagnostics echoing config
  values including secrets). All three predate this task; all three sit in
  the cluster it moved.
- No new config schema features. *(The `server` section was expected to
  be untouched; review found its refusals unattributed and its values
  unvalidated at all, so it gained `_config_error` routing and
  `_probe_config_server_argv`. The allowlist itself is unchanged.)*

## Affected Files

`src/anomaly_metric_creator/server_config.py` — the new leaf, and where the
work landed: the pre-existing `_load_serve_config`,
`_extract_serve_config_path`, `_strip_serve_config_arg`,
`_config_mapping_to_argv`, and `_parse_serve_args` moved there, and the new
`_config_error`, `_probe_config_generate_argv`,
`_probe_config_server_argv`, `_resolve_generate_parse_args`, and
`_vouch_no_flag_generate_keys` were added there.
`src/anomaly_metric_creator/server.py` keeps only the re-import block for the
historic `server.<name>` surface.
`tools/check_module_size.py` (`server.py` ceiling 2096 → 1976, a net -120).
*(Revised 2026-08-26: the plan was to bump the ceiling and defer extraction
to the `server.py` decomposition follow-up. Review rounds pushed the cluster
from +78 to +160 across four ceiling bumps, which is the growth the ratchet
exists to stop, so the deferred remedy was taken instead: the whole 283-line
cluster moved verbatim to `server_config.py`, a leaf importing nothing from
`server.py`, which re-imports every name. `server.py` now ends below where
this task found it.)*, `tests/test_server.py`, `README.md` (`--config` row),
`.trellis/spec/amc/backend/api-cli-server.md` § Serve Mode, `CLAUDE.md`.
*(Corrected 2026-08-26: neither CLAUDE.md nor the spec carried any
`--config` text, so both are additive, not an amended paragraph. A second
correction the same day: `_load_serve_config` was originally expected to be
untouched, but review rounds gave it a non-string-key guard and routed all
of its refusals through `_config_error`, and it then moved with the rest of
the cluster. Both this list and that note described the pre-extraction
layout for one round after the extraction landed.)*

## Risks And Edge Cases

- The probe must capture argparse's stderr (contextlib.redirect_stderr)
  so failure detail lands in the ValueError, not the console twice.
- The parametrized valid-keys test derives from the real surface (e.g.
  a curated list asserted non-empty, spot-covering common + advanced
  `--help-all` flags) — with the non-empty guard so it cannot go
  vacuously green (PRD acceptance).
- A generate key whose *value* is invalid now also fails at load with
  file attribution — strictly better; note it as intended in the PR.

## Validation

- Tests: unknown key → ValueError naming key-ish diagnostic + path;
  every sampled valid key loads; CLI-overrides-config precedence still
  green; silent-drop arm now loud.
- `pytest tests/test_server.py -n 0 -k config` + full suite.
