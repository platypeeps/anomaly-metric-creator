# Implementation Plan

1. Inspect current apply, generic-resource, resource-snapshot, and debug UI
   paths.
2. Add manifest loading and validation helpers in `server_ops.py`.
3. Change `_render_apply()` to return `CommandResult`, apply all parsed
   supported documents atomically, and preserve current filename fallback.
4. Add focused `tests/test_server.py` coverage for:
   - multi-document YAML apply,
   - JSON list apply,
   - dry-run side-effect protection,
   - invalid/unsupported manifest errors.
5. Improve debug UI resource-diff wording for created resources using existing
   mutation summary data.
6. Add deployment-scoped rollout lifecycle support for pause, resume, and undo,
   including `--to-revision` parsing and focused regression coverage.
7. Update README and canonical Trellis guidance.
8. Run targeted checks:
   - `.venv/bin/pytest tests/test_server.py -q -k "rollout or apply"`
   - `.venv/bin/pytest tests/test_server.py -q`
   - `git diff --check`

## Rollback Points

- Revert the `_render_apply()` helper changes if manifest parsing creates
  unintended side effects.
- Revert the rollout lifecycle helpers if command parsing accepts non-deployment
  targets or conflicts with the existing rollout status/history/restart
  behavior.
- Revert only `server_debug_ui.py` if UI polish is noisy but command behavior is
  correct.

## Validation Results

- [x] `.venv/bin/pytest tests/test_server.py -q -k "apply"` — 4 passed.
- [x] `.venv/bin/pytest tests/test_server.py -q -k "rollout or apply"` — 7
      passed.
- [x] `.venv/bin/pytest tests/test_server.py -q` — 87 passed, 2 skipped
      real-client smoke tests.
- [x] `.venv/bin/ruff check tests/` — passed.
- [x] `.venv/bin/pytest tests/test_trellis_placeholder_lint.py -q` — 9 passed.
- [x] `.venv/bin/pytest` — 1432 passed, 2 skipped real-client smoke tests.
- [x] Live test-server smoke for `/debug`, `/v1/debug/resources`, and
      `/v1/state` — debug shell served, namespace/configured UI strings present,
      and rollout mutation surfaced as `Paused`.
- [x] `git diff --check` — passed.

## PR Review Adjustment Results

- [x] Copilot review thread for non-UTF8 manifest files addressed by returning
      partial `kubectl.apply.manifest.read` instead of letting
      `UnicodeDecodeError` escape.
- [x] Copilot test-coverage thread addressed with single-object JSON manifest
      apply coverage and non-UTF8 manifest read-failure coverage.
- [x] CI-only `test_active_anomalies_does_not_copy_all_rows` race fixed by
      pausing the accelerated simulation clock before constructing the test
      window.
- [x] `.venv/bin/pytest tests/test_server.py -q -k "apply or active_anomalies_does_not_copy_all_rows"` — 7 passed.
- [x] `.venv/bin/pytest tests/test_server.py -q` — 89 passed, 2 skipped
      real-client smoke tests.
- [x] `.venv/bin/pytest -n 2 --dist loadfile -m "not heavy"` — 1389 passed,
      2 skipped real-client smoke tests.
- [x] `.venv/bin/pytest -n 0 -m heavy` — 45 passed.
- [x] `.venv/bin/ruff check tests/` — passed.
- [x] `git diff --check` — passed.
