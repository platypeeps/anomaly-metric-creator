# Compatibility Pointer: Persistence

This project does not use a general application database. The canonical
persistence and trace-store conventions now live in
[Operations, Security, and Logging](./operations-security-logging.md), while
trace-bundle API contracts live in [API, CLI, and Server](./api-cli-server.md).
Sources: `.trellis/spec/backend/index.md`;
`.trellis/spec/backend/operations-security-logging.md`;
`.trellis/spec/backend/api-cli-server.md`;
`src/anomaly_metric_creator/server_traces.py`;
`src/anomaly_metric_creator/trace_bundle.py`.

Do not add new conventions here. Update the focused specs above instead.
Sources: `.trellis/spec/backend/index.md`.
