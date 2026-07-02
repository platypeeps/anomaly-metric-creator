# Evaluate debug UI shell extraction

## Goal

Consider extracting the inline debug UI shell from server.py only when it reduces maintenance risk and remains strongly covered by endpoint behavior tests.

## Requirements

- Evaluate extracting the inline debug UI shell only if doing so reduces maintenance risk.
- Keep any extraction behavior-preserving and strongly covered through endpoint behavior tests.
- Preserve the existing `server.py` facade compatibility expectations, including `DEBUG_HTML` re-export behavior if still required.
- Avoid broad frontend rewrites; migrated backlog notes called for incremental debug UI changes.
- If extraction is not yet justified, document the decision and leave the existing structure intact.

## Acceptance Criteria

- [ ] The task starts with a concrete go/no-go decision for extraction based on current code structure and maintenance cost.
- [ ] If extraction proceeds, debug UI HTML/CSS/JS behavior remains equivalent through focused endpoint tests.
- [ ] If extraction is deferred, the PR records why and does not churn debug UI files unnecessarily.
- [ ] `tests/test_server.py` architecture-boundary expectations remain accurate.

## Notes

- Source: migrated server-mode debug UI backlog entry.
- Treat this as a maintenance decision, not an automatic extraction mandate.
