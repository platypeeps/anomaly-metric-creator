---
title: Expand Helm incident command coverage
status: planning
created: 2026-06-29
---
# Expand Helm incident command coverage

## Goal

Add Helm lint, dependency, repo, and chart metadata command behavior where it helps common incident workflows.

## Requirements

- Add Helm `lint`, `dependency`, `repo`, and chart metadata command behavior where it helps common incident workflows.
- Use existing Helm release state, values layering, chart metadata, and scenario profiles as the source of truth.
- Prefer focused command shapes over broad Helm emulation.
- Provide scenario-appropriate stdout/stderr, exit codes, and `CommandTrace` support status.
- Add unsupported trace coverage for adjacent Helm commands/options that remain unmodeled.

## Acceptance Criteria

- [ ] At least one Helm incident workflow command family is implemented with realistic command-mode behavior.
- [ ] Supported and unsupported Helm paths are distinguishable in command traces.
- [ ] Tests in `tests/test_server.py` cover selected Helm commands and nearby unsupported cases.
- [ ] Existing Helm install/upgrade/value-layering behavior remains unchanged.

## Notes

- Source: migrated server-mode compatibility backlog entry.
- Defer individual command families that do not map to a concrete workshop or incident workflow.
