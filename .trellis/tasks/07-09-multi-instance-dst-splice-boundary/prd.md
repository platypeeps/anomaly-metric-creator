# Decide or support multi-instance DST splice behavior

## Backlog consolidation context

- **Source:** backlog consolidation sweep, 2026-07-09.
- **Historical source:** the removed server-mode handoff note as last present
  at `e52ee6a^`; that live file was intentionally removed in `e52ee6a`.
- **Current source:** `README.md` and `CLAUDE.md` still document one remaining
  multi-instance boundary: `--inject-dst-artifact-day > 0` remains
  incompatible with `--instances-per-component > 1` and `--instance-config`.
- **Consolidation result:** the old server-mode follow-ups are already
  represented by active or archived Trellis tasks. This task tracks the only
  remaining current-doc item found without an active Trellis task.

## Goal

Decide whether the intentional `--inject-dst-artifact-day` boundary for
multi-instance generation should stay documented as unsupported or be
implemented with a safe non-monotonic timestamp model.

## Existing follow-up-to-task mapping

- `kubectl get --watch` and API watch semantics:
  `06-29-server-watch-semantics`.
- Additional `kubectl logs` refinements:
  `06-29-kubectl-logs-refinements`.
- `kubectl events` richer sorting/filtering:
  `06-29-kubectl-events-compatibility`.
- More realistic `kubectl exec` outputs:
  `06-29-kubectl-exec-outputs`.
- More complete `kubectl port-forward` lifecycle:
  `06-29-kubectl-port-forward-lifecycle`.
- Helm `lint`, `dependency`, `repo`, and chart metadata commands:
  `06-29-helm-incident-command-coverage`.
- Optional persisted mutation state and explicit unsupported subresource
  handling: `06-29-persisted-server-mutation-state`.
- Debug UI shell extraction: archived as completed-by-prior-work in
  `archive/2026-07/06-29-debug-ui-shell-extraction`.
- Persistence/search, security/operations, and architecture cleanup sections in
  the final historical handoff said no known follow-ups remain beyond
  workshop-driven polish, so no new task was created for those sections.

## Requirements

- Make an explicit maintainer decision:
  - **Keep unsupported:** retain the current parser/helper guards, make the
    intentional boundary easy to find in docs/specs, and add or refresh tests
    only if the guard is under-covered.
  - **Implement support:** design a non-monotonic timestamp model that works
    for wide CSVs, long-form CSVs, gauges, schema validation, combine, and OTEL
    gauge streaming without silently corrupting ordering assumptions.
- Preserve the default single-instance and non-DST behaviors byte-for-byte
  unless the implementation path deliberately changes documented output.
- Keep any implementation behavior explicit at parse/validation time; do not
  make a formerly rejected combination partially work in only one artifact
  family.
- Update `README.md`, `CLAUDE.md`, and relevant Trellis specs so the chosen
  posture is discoverable from the canonical guidance, not just this task.

## Acceptance Criteria

- [ ] The supported-vs-unsupported decision is recorded with rationale in the
      PRD or a follow-on design note before implementation starts.
- [ ] If kept unsupported, user-facing and agent-facing docs name the
      `--inject-dst-artifact-day` plus multi-instance incompatibility as an
      intentional design boundary.
- [ ] If implemented, tests cover the affected artifact families:
      per-component CSVs, long-form/gauges, schema/validate, combine, and OTEL
      gauge streaming.
- [ ] Error messages for rejected combinations remain clear and point to the
      supported alternative (`--inject-dst-artifact-day 0`) when unsupported.
- [ ] No duplicate Trellis task is created for the same boundary.

## Notes

- This is probably a decision task first. If support is chosen, add `design.md`
  before starting implementation because the timestamp model touches multiple
  artifact families and ordering assumptions.
- Current docs use both "intentional boundary" and "only remaining gate"
  language; settle on one clear posture when the task is executed.
