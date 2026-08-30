# Cross-Layer Thinking Guide

## Map the Contract

Use this guide when a change crosses any of these boundaries: CLI parser to
generation, generation to output files, output files to `schema.json`,
`schema.json` to validator, server command/API to trace store, trace export to
offline `trace-bundle`, or the specs under `docs/spec/` to platform adapters. Sources:
`docs/spec/amc/backend/api-cli-server.md`;
`docs/spec/amc/backend/operations-security-logging.md`;
`docs/spec/amc/backend/documentation-review.md`;
`docs/application-flow.md`; `src/anomaly_metric_creator/`;
`tests/test_validate_output.py`; `tests/test_trace_bundle.py`;
`tests/test_server.py`.

For each boundary, identify the owner, input shape, output shape, validation
location, error surface, tests, and docs that mention it before editing.
Sources: `docs/spec/amc/backend/testing-quality.md`; `CLAUDE.md`;
`README.md`; `docs/application-flow.md`; `tests/`.

## Common Repository Boundaries

CLI changes cross `README.md`, parser help, reconciliation/defaults,
subcommand bypass behavior, tests, and docs diagrams. Sources:
`docs/spec/amc/backend/api-cli-server.md`; `README.md`;
`docs/application-flow.md`; `src/anomaly_metric_creator/legacy.py`;
`tests/test_cli_surface.py`; `tests/test_args.py`.

Scenario changes cross `SCENARIOS`, registry validation, README catalog rows,
ops profiles, server scenario endpoint/debug UI, and tests. Sources:
`docs/spec/amc/backend/scenarios-and-data.md`;
`src/anomaly_metric_creator/legacy.py`;
`src/anomaly_metric_creator/server_ops.py`; `README.md`;
`tests/test_scenarios.py`; `tests/test_server.py`.

Schema/output changes cross file writers, `_EMIT_ARTIFACT_FILES`,
`schema.json`, validator logic, README output docs, and golden/hash tests.
Sources: `docs/spec/amc/backend/api-cli-server.md`;
`src/anomaly_metric_creator/legacy.py`; `README.md`;
`tests/test_schema_file.py`; `tests/test_validate_output.py`;
`tests/test_emit_selection_hygiene.py`.

Trace/search changes cross live command traces, JSONL/SQLite persistence,
export/import payloads, debug search endpoints, and offline `trace-bundle`
commands. Sources: `docs/spec/amc/backend/operations-security-logging.md`;
`src/anomaly_metric_creator/server_traces.py`;
`src/anomaly_metric_creator/server.py`;
`src/anomaly_metric_creator/trace_bundle.py`; `tests/test_server.py`;
`tests/test_trace_bundle.py`.

Platform adapter changes cross the rendered skill trees, hooks, agents,
and platform-specific config. Keep durable project conventions in specs and
verify the adapters still load the specs under `docs/spec/`. Sources:
`docs/spec/amc/backend/documentation-review.md`; `scripts/sync-agent-skills.py`;
`.agents/`; `.codex/`; `.claude/`; `.gemini/`; `.github/`; `.opencode/`.

## Quick Checklist

- Did I identify every reader and writer of the changed format? Sources:
  `src/anomaly_metric_creator/`; `tests/`; `README.md`.
- Did I validate untrusted/read-back data on the reader side? Sources:
  `docs/spec/amc/backend/testing-quality.md`;
  `src/anomaly_metric_creator/legacy.py`;
  `src/anomaly_metric_creator/server_traces.py`;
  `src/anomaly_metric_creator/trace_bundle.py`.
- Did I update user-facing docs and the `docs/spec/` tree together? Sources:
  `docs/spec/amc/backend/documentation-review.md`; `README.md`; `docs/`.
- Did I run targeted tests for the boundary that changed? Sources:
  `docs/spec/amc/backend/testing-quality.md`; `tests/`; `pyproject.toml`.
