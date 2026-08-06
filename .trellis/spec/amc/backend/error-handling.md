# Compatibility Pointer: Error Handling

The canonical validation, CLI error, HTTP error, and Kubernetes `Status`
conventions now live in [API, CLI, and Server](./api-cli-server.md),
[Operations, Security, and Logging](./operations-security-logging.md), and
[Testing and Quality](./testing-quality.md). Sources:
`.trellis/spec/amc/backend/index.md`; `.trellis/spec/amc/backend/api-cli-server.md`;
`.trellis/spec/amc/backend/operations-security-logging.md`;
`.trellis/spec/amc/backend/testing-quality.md`;
`src/anomaly_metric_creator/server.py`; `src/anomaly_metric_creator/legacy.py`;
`tests/test_server.py`; `tests/test_validate_output.py`.

Do not add new conventions here. Update the focused specs above instead.
Sources: `.trellis/spec/amc/backend/index.md`.
