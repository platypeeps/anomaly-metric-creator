# Symmetric --config generate-key validation — Implementation Plan

## Execution Order

1. Branch from `main`. Read `_load_serve_config` +
   `_config_mapping_to_argv` end to end; list every conversion arm and
   mark any silent-skip path.
2. Implement the probe parse (SystemExit trap + redirected stderr →
   `ValueError` shaped like `unknown_server`, naming the config path).
3. Make silent-drop arms in `_config_mapping_to_argv` loud (same error
   shape, naming the key).
4. Tests: typo'd key (`componentss`) → load-time ValueError; sampled
   valid keys parametrized (non-empty guard); precedence regression;
   YAML and JSON config forms both covered.
5. CLAUDE.md serve `--config` paragraph: one sentence on symmetric
   validation.
6. Draft PR → checklist → ready → merge.

## Validation Plan

```bash
.venv/bin/pytest tests/test_server.py -n 0 -k "config"
.venv/bin/pytest && .venv/bin/pre-commit run --all-files
```

Manual: `amc serve --config bad.yaml` (typo'd generate key) — error
names the file before any generation output appears.

## Documentation And Spec Updates

- CLAUDE.md serve-mode `--config` description.

## Review Notes

- The probe-parse derivation is the review headline: source of truth is
  the real parser, so a new generate flag is accepted automatically with
  zero maintenance — state the July-8 build_parser-impractical finding
  as the reason for this shape.

## Follow-Ups

- After decomp step 8 lands, the probe still works unchanged; if
  cli_args.py ever exposes parser introspection, the probe can be
  swapped for set-membership — optional, not planned.
