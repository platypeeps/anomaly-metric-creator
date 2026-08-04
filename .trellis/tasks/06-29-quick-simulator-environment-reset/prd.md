# Add quick simulator environment reset workflow

## Goal

Provide a quick, documented reset path for an interactive AMC simulator environment that clears mutable state and restores a predictable baseline for the selected failure mode.

## Requirements

- Provide a quick reset workflow for an already-running interactive simulator environment.
- Clear mutable Kubernetes/Helm overlay state so inspected resources return to the selected scenario baseline.
- Decide whether reset should also refresh generated artifacts, command traces, simulation clock, or continuous-generation counters; document the chosen behavior clearly.
- Preserve the existing `POST /v1/mutations/reset` behavior or evolve it compatibly if a broader environment reset endpoint/command is needed.
- Make the reset path easy to invoke from common inspection workflows: debug UI, curl, and any launcher output added by the related launcher task.
- Keep resets deterministic and safe for local workshop/demo use.

## Acceptance Criteria

- [x] A user has a copyable command or UI path to reset the interactive environment without restarting the server.
- [x] After reset, created/deleted resources, workload scale/restart/delete overlays, Helm release overlays, and extra mutation events return to the selected scenario baseline.
- [x] If generated artifacts, traces, or clock state are intentionally not reset, the documentation says so explicitly.
- [x] Focused server tests cover the reset contract and at least one realistic post-reset inspection command.
- [x] Existing debug UI reset behavior and `/v1/mutations/reset` callers remain compatible, or migration notes/tests cover the new behavior.

## Notes

- Related to `06-29-interactive-failure-mode-launcher`, but independently useful for current `amc serve` users.
- This overlaps conceptually with persisted mutation state; keep this task focused on reset ergonomics and baseline restoration, not restart persistence.
- Add a short `design.md` before implementation if reset scope expands beyond the current mutation overlay endpoint.
