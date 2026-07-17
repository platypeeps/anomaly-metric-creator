# Symmetric --config generate-key validation — Design (SD Work Designs, 2026-07-17)

## Overview

`_load_serve_config` allowlists `server` keys (`unknown_server`,
server.py:1339) but only dict-type-checks the `generate` map before
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

1. In `_load_serve_config`, after `_config_mapping_to_argv` produces the
   generate argv, run a **probe parse**: call `legacy.parse_args(argv)`
   inside a trap that captures `SystemExit` + intercepted stderr.
   On failure, raise `ValueError` shaped like the existing
   `unknown_server` message — naming the config path and embedding the
   parser's own diagnostic (which names the bogus `--componentss` flag,
   giving per-key attribution). The probe is exactly the parse
   `serve_main` would run later, moved earlier with file attribution —
   no behavior it could reject survives today anyway.
2. Audit `_config_mapping_to_argv` (server.py:1377) for silent-drop
   arms (the PRD's "collides with nothing" case): any key shape it
   cannot convert to a flag must raise the same `ValueError` naming the
   key, not skip it.
3. Precedence untouched: explicit CLI flags are appended after config
   flags and still win; the probe validates the config-derived argv
   alone (a config value the CLI overrides must still be *valid* — same
   rule `server` keys already live under).

Probe-safety audit (record results in the PR): `parse_args` is
side-effect-free apart from `set_defaults` env reads — but its
validation gates include file-existence checks (`--instance-config`),
which is correct probe behavior (the later real parse would fail
identically; earlier + attributed is the improvement).

## Boundaries And Non-Goals

- No parse_args refactor (decomp step 8 owns that move; this design
  works identically before and after it — the probe calls the same
  public entrypoint).
- No new config schema features; `server`-key validation untouched.

## Affected Files

`src/anomaly_metric_creator/server.py` (`_load_serve_config`,
`_config_mapping_to_argv`), `tests/test_server.py` (config-load
coverage), CLAUDE.md serve `--config` paragraph.

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
