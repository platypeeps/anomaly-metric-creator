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
