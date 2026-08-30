# SQLite trace INSERT dedupe — Implementation Plan

One PR. Net-negative diff in one owned module plus new tests.

## Execution Order

1. **Capture a pre-change oracle.** Record a few traces through a
   `CommandTraceStore(sqlite_path=...)`, then dump `command_traces` and
   `command_traces_fts` rows to a scratch file. Do the same through the
   import/replace path. This is the byte-level comparison target for step 4 —
   capture it *before* editing, not after.
2. **Add the row writer.** New private method on the store, placed directly
   above `_insert_sqlite`:

   ```python
   def _insert_trace_row(
       self,
       conn: sqlite3.Connection,
       trace: CommandTrace,
       payload: dict[str, Any],
       *,
       delete_fts_first: bool,
   ) -> None:
   ```

   Body is the verbatim `INSERT OR REPLACE INTO command_traces` execute from
   `:607-641` — the `conn.execute(...)` call only, **not** the `payload =
   trace.to_dict()` binding at `:605`, which stays at each call site (see
   `prd.md`: it is outside the lock on the insert path and inside it on the
   replace path, and the helper must not change that). Both
   `json.dumps(..., sort_keys=True)` calls do move, since both already run
   inside the lock on both paths. Then `if self._sqlite_fts_enabled:` guarding
   an optional `DELETE FROM command_traces_fts WHERE trace_id = ?` under
   `delete_fts_first`, then the verbatim FTS insert.

   `payload` is annotated `dict[str, Any]`, matching `to_dict()`'s return —
   note `dict[...]` is safe inside this class body, only `list[...]` is
   shadowed.
3. **Rewrite both call sites**, each keeping its own `payload` binding exactly
   where it is today.
   - `_insert_sqlite` (`:604`): keep `payload = trace.to_dict()` at `:605`
     *before* `with self._locked_conn()`, then
     `self._insert_trace_row(conn, trace, payload, delete_fts_first=True)` →
     `_enforce_sqlite_retention(conn)` → `self._sqlite_gen += 1`.
   - `_replace_sqlite_traces` (`:854`): keep its two leading `DELETE`s and the
     per-row `payload = trace.to_dict()` at `:860` *inside* the loop, then
     `self._insert_trace_row(conn, trace, payload, delete_fts_first=False)`,
     then `_enforce_sqlite_retention(conn)`, `self._sqlite_gen += 1`, and the
     trailing `self._load_sqlite_tail()` outside the `with`.
   - Rationale for `delete_fts_first`: the *insert* path needs it because it
     can overwrite an existing id; the *replace* path does not because it
     already cleared the whole FTS table. Parent `design.md` stated this
     backwards and has been corrected.
4. **Diff against the oracle.** Re-dump both tables and confirm byte-identical
   rows on both paths. This is the behavior-identity evidence for the PR body;
   a green suite alone is not it, because no current test compares every
   column of the replace path.
5. **Add the missing tests** in `tests/test_server.py`, beside the existing
   `test_command_trace_sqlite_*` family (reuse the module's `_trace()` helper
   and `tmp_path`; no new fixture):
   - **full-column assertion on the raw row.** Open the db with `sqlite3` and
     `SELECT * FROM command_traces WHERE id = ?`, then compare each of the 21
     columns against the source trace. Do *not* assert via reloaded
     `to_dict()` — every read path rebuilds from `payload_json` alone
     (`:589`, `:700`, `:713`), so that form of the test passes even if the
     other 20 columns are dropped. Cover both write paths: one trace via
     `record()`, one via `import_payload()`.
   - **FTS rows asserted directly**, never through `search()`, which falls
     back to LIKE on `OperationalError` (`:766-775`):
     `SELECT count(*) FROM command_traces_fts` after an import equals the
     imported count, and the FTS row's seven columns match the trace.
   - **`delete_fts_first` regression:** record a trace, record a *different*
     trace with the same id, then assert
     `SELECT count(*) FROM command_traces_fts WHERE trace_id = ?` is exactly
     1 and holds the second trace's text. Without the per-row delete this is
     2 — that is the assertion that would catch passing `False` here.
   - Guard all three FTS assertions on `store._sqlite_fts_enabled`
     (`pytest.skip` when False) so a build without FTS5 cannot report a
     false pass.
6. **Flip the ledger.** A-031 → `status: fixed`, in this same PR (epic
   convention; see `prd.md` — the ledger states no such rule itself).
   Also correct its `fix:` sketch at `ledger.md:342`, which still reads
   `_insert_trace_row(conn, trace, *, delete_fts_first)` without `payload`.
   Rewrite the evidence line to name **symbols**, not line numbers —
   `CommandTraceStore._insert_sqlite` vs `._replace_sqlite_traces` — because
   this very extraction moves both methods, so `:604`/`:854` would be stale
   on merge exactly the way the audit's original `:442`/`:691` went stale.

## Validation Plan

```bash
# focused first
.venv/bin/pytest tests/test_server.py -k sqlite -n 0
.venv/bin/pytest tests/test_server.py -n 0
# structural acceptance (counts verified against HEAD 802eb46)
grep -c "INSERT OR REPLACE INTO command_traces" \
  src/anomaly_metric_creator/server_traces.py     # 2 now -> expect 1
grep -c "INSERT INTO command_traces_fts(" \
  src/anomaly_metric_creator/server_traces.py     # 3 now -> expect 2
# typing: module is NOT gated; hold the pre-existing diagnostics.
# Capture the baseline BEFORE editing. Compare diagnostic *identities*, not
# the count (which hides a new error displacing an old one) and not the raw
# output (whose line numbers all shift once the helper is inserted above
# them). Strip the line number, keep file + message + error code, sort:
mypy_ids() { .venv/bin/mypy --strict "$1" \
  | sed -E 's/^([^:]+):[0-9]+:/\1:/' | grep -v '^.*note:' | sort; }
mypy_ids src/anomaly_metric_creator/server_traces.py > /tmp/mypy-baseline.txt
# ... make the change ...
mypy_ids src/anomaly_metric_creator/server_traces.py > /tmp/mypy-after.txt
diff /tmp/mypy-baseline.txt /tmp/mypy-after.txt   # expect no diff
# broad
.venv/bin/pytest && .venv/bin/pre-commit run --all-files
.venv/bin/python tools/check_mypy_gate.py                             # 34 modules
```

`tests/test_trace_bundle.py` is deliberately absent: it exercises
`trace_bundle.py` offline analysis, never builds a `CommandTraceStore`, and
cannot witness either write path. Parent `design.md` named it in error and has
been corrected.

Failure means: any grep count above off, any pre/post oracle row differing,
any diff in the normalized mypy diagnostic set, or any suite regression.

## Documentation And Spec Updates

- None expected. `server_traces.py` keeps its owner row in the CLAUDE.md module
  map; no public name, module boundary, or artifact changes. If review disagrees,
  the note belongs in `operations-security-logging.md` (command-trace
  persistence), not CLAUDE.md.

## Review Notes

- The claim is behavior-identity, so the PR body must state the evidence:
  pre/post table dumps on both write paths, not just a green suite.
- Reviewer-sensitive spot: `delete_fts_first=False` is correct for the replace
  path *only because* it clears the whole FTS table up front (`:858`). If that
  bulk delete is ever removed, the flag becomes wrong. Say so in the PR.
- Lock scope is the highest-risk detail in this diff: `payload =
  trace.to_dict()` must stay outside `_locked_conn()` on the insert path. A
  reviewer skimming for "verbatim move" will not notice it drifting inward,
  and no test would fail — it is a latency regression, not a correctness one.
  Call it out explicitly in the PR body.
- Second spot: the FTS guard must stay `self._sqlite_fts_enabled` inside the
  helper. Hoisting it to the call sites reintroduces the duplication.
- Third spot: no `list[...]` annotation may enter the helper's signature —
  `CommandTraceStore.list` shadows the builtin inside the class body (see
  `prd.md` § "Known hazard"). A new `valid-type` diagnostic in the normalized
  mypy set means this was hit.

## Rollback Points

- Precondition: start from a clean tree on the feature branch and commit the
  extraction before moving on, so rollback is a revert rather than a discard.
- After step 3 (extraction, no tests yet): revert that commit. Do **not**
  reach for `git checkout -- src/anomaly_metric_creator/server_traces.py` —
  if the working tree holds any other edit to that file, it is destroyed with
  no recovery. If the extraction is genuinely uncommitted, `git stash push`
  that one path instead, which is reversible.
- After step 5: revert the commit; the new tests are additive and the ledger
  flip is a separate hunk.

## Follow-Ups

- Siblings `08-06-conftest-helper-consolidation` (A-033 + A-037) and
  `08-06-otlp-capture-fixture` (A-032) — explicitly outside this PR.
- **New, found during this task's planning review:** `CommandTraceStore.list`
  shadows the builtin `list` inside the class body, producing 10 of the 11
  strict-mypy errors and keeping `server_traces.py` out of the clean gate.
  (The 11th is unrelated: `no-any-return` at `server_traces.py:687`.)
  Fixing it means
  either renaming the method (public-surface change) or rewriting the in-class
  annotations to `builtins.list[...]` / `typing.List[...]`. File a task; do not
  fold it into this PR.
