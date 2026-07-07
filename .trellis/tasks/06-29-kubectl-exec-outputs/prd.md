# Add realistic kubectl exec responses

## Goal

Make kubectl exec command-mode output more realistic for command-specific incident probes while keeping behavior simulator-backed.

## Requirements

- Make `kubectl exec` command-mode responses more realistic for command-specific incident probes.
- Choose command outputs that map cleanly to simulator scenarios and resource state.
- Provide scenario-appropriate stdout/stderr and exit codes for supported exec probes.
- Record partial/unsupported traces for unknown pods, unsupported commands, unsupported flags, and other nearby gaps.
- Avoid simulating an arbitrary shell; support explicit, testable command shapes only.

## Acceptance Criteria

- [ ] At least one meaningful incident-oriented `kubectl exec` command shape returns realistic simulator-backed output.
- [ ] Unknown or unsupported exec requests produce clear error output and trace metadata.
- [ ] Tests cover supported, partial, and unsupported exec paths in `tests/test_server.py`.
- [ ] No second pod/container state model is introduced.

## Notes

- Source: migrated server-mode compatibility backlog entry.
- Keep the scope command-specific and explicit rather than shell-complete.
- **Current state (verified 2026-07-06):** `kubectl exec` is already
  supported with simulated env/curl/generic outputs via `_render_exec`
  ([server_ops.py:4089](src/anomaly_metric_creator/server_ops.py:4089)).
  Two concrete defects to fix as part of this task:
  1. The `"--" in parsed.positionals` guard at
     [server_ops.py:4092](src/anomaly_metric_creator/server_ops.py:4092) is
     **dead code** — `--` is listed in `_BOOL_FLAGS`
     ([server_ops.py:1102](src/anomaly_metric_creator/server_ops.py:1102)),
     so `_split_flags` consumes it before it can reach positionals.
  2. Flag-shaped tokens after `--` (e.g. `kubectl exec pod -- ls -la`) are
     swallowed by the generic `token.startswith("-")` arm
     ([server_ops.py:1474](src/anomaly_metric_creator/server_ops.py:1474)),
     so the reconstructed command loses `-la` — lossy argument fidelity.
- **Eval-mode coupling:** the current `env`/`printenv` output embeds
  `SCENARIOS=<active slugs>`
  ([server_ops.py:4100](src/anomaly_metric_creator/server_ops.py:4100)) —
  a rubric leak flagged by the 2026-07-06 review (tracked separately as the
  eval-mode ground-truth-wall task). Any new exec outputs added here must
  stay inside that wall: no active-scenario identifiers, only observable
  symptoms.
