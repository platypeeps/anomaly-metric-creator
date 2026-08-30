---
title: Model kubectl port-forward lifecycle
status: planning
created: 2026-06-29
---
# Model kubectl port-forward lifecycle

## Goal

Implement more complete kubectl port-forward lifecycle behavior for the simulator when common incident workflows need it.

## Requirements

- Model more complete `kubectl port-forward` lifecycle behavior for common incident workflows.
- Keep the lifecycle bounded and simulator-safe; do not open real network tunnels unless explicitly designed and guarded.
- Return realistic command-mode output and exit behavior for supported targets.
- Capture partial/unsupported traces for unsupported target kinds, ports, namespaces, and lifecycle options.
- Preserve existing security and remote-bind guardrails.

## Acceptance Criteria

- [ ] Supported port-forward requests produce realistic lifecycle/status output without exposing unsafe network behavior.
- [ ] Unsupported or unsafe requests fail clearly and are visible in command traces.
- [ ] Tests cover success and failure/lifecycle edge cases in `tests/test_server.py`.
- [ ] Existing server security and command API tests continue to pass.

## Notes

- Source: migrated server-mode compatibility backlog entry.
- This likely needs a short `design.md` before implementation because lifecycle behavior can affect security expectations.
