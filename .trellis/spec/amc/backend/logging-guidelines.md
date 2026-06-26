# Compatibility Pointer: Logging

The canonical logging, OTEL diagnostics, structured request log, and redaction
conventions now live in
[Operations, Security, and Logging](./operations-security-logging.md). Sources:
`.trellis/spec/amc/backend/index.md`;
`.trellis/spec/amc/backend/operations-security-logging.md`;
`src/anomaly_metric_creator/server.py`;
`src/anomaly_metric_creator/server_traces.py`;
`src/anomaly_metric_creator/legacy.py`; `tests/test_server.py`;
`tests/test_redact_sensitive_headers.py`.

Do not add new conventions here. Update `operations-security-logging.md`
instead. Sources: `.trellis/spec/amc/backend/index.md`;
`.trellis/spec/amc/backend/operations-security-logging.md`.
