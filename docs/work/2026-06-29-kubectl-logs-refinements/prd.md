---
title: Refine kubectl logs incident output
status: planning
created: 2026-06-29
---
# Refine kubectl logs incident output

## Goal

Add kubectl logs refinements for incident workflows, such as duration-based --since, timestamped output, and richer multi-container pod histories when justified by client workflows.

## Requirements

- Improve `kubectl logs` compatibility only where incident workflows need richer behavior than the current simulator output.
- Candidate refinements include duration-based `--since`, timestamped output, and richer multi-container pod histories.
- Keep log behavior scenario-appropriate and backed by existing generated log-stream inputs or simulator state.
- Record supported, partial, or unsupported `CommandTrace` results that make nearby gaps visible in the debug UI and persisted traces.
- Avoid broad log-generation rewrites unless they are required for the selected incident workflow.

## Acceptance Criteria

- [ ] At least one selected `kubectl logs` refinement is implemented with realistic stdout/stderr and exit-code behavior.
- [ ] Multi-container or unsupported option behavior is explicit and covered rather than silently ignored.
- [ ] Tests in `tests/test_server.py` cover the refined behavior plus a nearby unsupported or partial case.
- [ ] Existing log-stream, command-trace, and debug UI behavior continues to pass.

## Notes

- Source: migrated server-mode compatibility backlog entry.
- Treat this as workshop-driven polish; defer if no concrete incident workflow benefits.
- **Current state (verified 2026-07-06):** `--since-time`
  ([server_ops.py:2744](src/anomaly_metric_creator/server_ops.py:2744)),
  `--tail` ([server_ops.py:2753](src/anomaly_metric_creator/server_ops.py:2753)),
  and `-c/--container` (validated against the component,
  [server_ops.py:2686](src/anomaly_metric_creator/server_ops.py:2686)) are
  handled. Two verified gaps to prioritize: **`--since` (duration form) is
  parsed but silently not applied** in `_render_pod_logs` — a silent no-op
  is worse than an unmodeled flag and should be first; `--timestamps` is not
  in `_MODELED_FLAGS`, so it downgrades the trace to `partial` via
  `_with_flag_support`
  ([server_ops.py:1705](src/anomaly_metric_creator/server_ops.py:1705)).
