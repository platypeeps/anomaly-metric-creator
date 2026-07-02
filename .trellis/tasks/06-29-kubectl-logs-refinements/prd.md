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
