---
title: Add interactive failure-mode environment launcher
status: done
created: 2026-06-29
---
# Add interactive failure-mode environment launcher

## Goal

Provide a documented command or wrapper that starts AMC serve with a chosen failure mode/scenario, leaves the simulator running for interactive debug UI, kubectl, and Helm inspection, and prints the useful connection details.

## Requirements

- Provide a clear command or wrapper for launching an interactive simulator environment from a chosen failure mode.
- Map user-facing "failure mode" language to AMC's existing scenario model (`--scenarios`, and `--signal-level` where applicable) without introducing a duplicate scenario selector.
- Start `amc serve` with generated artifacts for the selected scenario and leave the server running for interactive inspection.
- Print or document the key inspection affordances: debug UI URL, kubeconfig fetch command, example `kubectl` commands, and example Helm commands.
- Preserve existing serve-mode security defaults: loopback bind by default, explicit auth/remote-bind behavior, and no accidental exposure for workshop shortcuts.
- Keep the launcher compatible with existing serve-mode config files and normal generator passthrough flags.

## Acceptance Criteria

- [ ] A user can launch a selected failure-mode/scenario environment with one documented command or script.
- [ ] The launched environment stays running until interrupted and serves the debug UI, command API, Kubernetes facade, and Helm-compatible resources.
- [ ] Startup output includes enough copyable commands to inspect the environment with browser, `kubectl`, and Helm.
- [ ] Invalid or ambiguous failure-mode/scenario input fails with a helpful message and points to the scenario catalog.
- [ ] Focused tests or smoke coverage verify argument mapping and startup/help output without requiring a long-running manual server in CI.

## Notes

- This is a workflow/usability task around existing `amc serve`; it should not duplicate the simulator's scenario registry.
- Consider whether this belongs as a new subcommand, a documented script, or a serve-mode convenience flag during design.
- Read `.trellis/spec/amc/backend/api-cli-server.md` and `.trellis/spec/amc/backend/operations-security-logging.md` before implementation.
