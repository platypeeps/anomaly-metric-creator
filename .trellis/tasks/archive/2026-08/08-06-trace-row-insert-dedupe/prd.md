# Dedupe the SQLite trace INSERT block across the two store methods

Parent: `07-17-audit-test-harness-dedupe` (child 1 of 3).
Design of record: parent `design.md` § "PR 1 — A-031 (production,
behavior-identical)". This task is lightweight — one verbatim extraction with
a single parameterized difference — so it carries `prd.md` + `implement.md`
and does not restate a separate `design.md`.

## Audit context

- **Source:** 2026-07-17 full repo audit @ b0df00b — `.trellis/audit/ledger.md`.
- **Ledger item:** A-031 (P2 · S · bloat).
- Ledger line numbers (`:442` vs `:691`) are stale; the blocks are now
  `server_traces.py:604` (`_insert_sqlite`) and `:854`
  (`_replace_sqlite_traces`). Verified live at HEAD 802eb46.

## Goal

One `command_traces` INSERT and one `command_traces_fts` row INSERT in the
source, so a column added to `CommandTrace` cannot be wired into the live
insert path while the bundle-import path silently drops it.

## Requirements

- Extract a single row-writer used by both `_insert_sqlite` and
  `_replace_sqlite_traces`:
  `_insert_trace_row(conn, trace, payload, *, delete_fts_first)`.
  This extends the ledger's `fix:` sketch (`ledger.md:342`), which reads
  `_insert_trace_row(conn, trace, *, delete_fts_first)` — no `payload`. The
  extra parameter is required, for the lock-scope reason below; the closing
  PR updates that sketch line along with the status flip.
- **The helper must not call `trace.to_dict()`.** Each caller keeps computing
  `payload` exactly where it does today and passes it in. This is not a style
  choice: `_insert_sqlite` computes it at `:605`, *outside* its
  `with self._locked_conn()` at `:606`, while `_replace_sqlite_traces`
  computes it at `:860`, *inside* the lock. A helper that computed it
  internally would pull that work under the sqlite lock on every recorded
  command — a real latency change, and the opposite of the off-lock
  discipline `test_command_trace_jsonl_persistence_writes_off_the_ring_lock`
  exists to protect.
- Both SQL strings move **verbatim**: the same column list, the same 21
  placeholders, the same `INSERT OR REPLACE` / `INSERT` verbs, and the same
  `json.dumps(..., sort_keys=True)` calls for `active_scenarios_json` and
  `payload_json`.
- The only behavioral parameter is the per-row
  `DELETE FROM command_traces_fts WHERE trace_id = ?` that `_insert_sqlite`
  issues (`:644`) and `_replace_sqlite_traces` does not — the replace path
  clears the whole FTS table once up front (`:858`).
- The `self._sqlite_fts_enabled` guard keeps gating the FTS write, so a store
  with FTS disabled writes exactly the rows it writes today.
- Everything outside the extracted rows is untouched: the `_locked_conn()`
  scope, `_enforce_sqlite_retention(conn)`, the `_sqlite_gen` bump, the
  replace path's leading `DELETE`s, and its trailing `_load_sqlite_tail()`.

## Constraints

- Production code in an owned module (`server_traces.py`) — behavior-identical,
  no schema change, no persisted-format change, no public-name change.
- Not a `legacy.py` extraction: the epic's re-import stub pattern does not
  apply. This is a private method on one store class.
- No new module. The file is 1,025 lines and the change is net-negative.

## Acceptance criteria

- [x] `grep -c "INSERT OR REPLACE INTO command_traces"
      src/anomaly_metric_creator/server_traces.py` returns `1`, and the
      `INSERT INTO command_traces_fts(` count drops from 3 to 2 (the remaining
      one is the schema/backfill path at `:571`, not a per-row write).
- [x] `.venv/bin/pytest tests/test_server.py -n 0` green — this is the whole
      oracle. Parent `design.md` also named `tests/test_trace_bundle.py`; that
      is wrong and is corrected there. That file covers `trace_bundle.py`
      offline analysis and never constructs a `CommandTraceStore`, so it
      cannot witness either SQLite write path. `import_payload` — the entry
      point into `_replace_sqlite_traces` — is exercised only in
      `tests/test_server.py`.
- [x] A round-trip test proves the replace path still writes every column —
      asserted by reading the **raw `command_traces` row** with a direct
      `SELECT`, not by comparing `to_dict()` after reload. Comparing reloaded
      traces proves nothing here: every read path (`_load_sqlite_tail:589`,
      `_list_sqlite:700`, `_get_sqlite:713`, and search) reconstructs the
      trace from `payload_json` alone, so drift in any of the other 20
      columns is invisible to it. Those columns are what the WHERE clauses
      and FTS filter on, which is exactly the breakage this task prevents.
- [x] FTS assertions read the `command_traces_fts` table directly. A passing
      `search()` does not prove an FTS write: on `sqlite3.OperationalError`
      search silently falls back to LIKE over `command_traces`
      (`:766-775`), and the existing
      `test_command_trace_sqlite_search_reports_backend_and_schema`
      (`test_server.py:1791`) deliberately accepts either backend — its
      assertion at `:1806` is `search["search_backend"] in {"fts5", "like"}`.
      Required:
      (a) after the replace path, `SELECT count(*) FROM command_traces_fts`
      matches the imported trace count; (b) re-recording an existing trace id
      leaves exactly one row for that `trace_id` (the `delete_fts_first=True`
      path); (c) the test skips or asserts explicitly when
      `store._sqlite_fts_enabled` is False, so a no-FTS build does not report
      a false pass.
- [x] Full suite + `pre-commit run --all-files` green; `check_mypy_gate.py`
      still clean at its 34 modules.
- [x] `server_traces.py` is **not** one of those 34 modules
      (`tools/check_mypy_gate.py:17-52`) and fails `mypy --strict` today with
      11 pre-existing errors, so "gate stays clean" says nothing about this
      file. The real criterion: `mypy --strict` on the module produces output
      **identical to a baseline captured before the edit** — diffed line by
      line, not compared by count. An equal count of 11 would still pass if a
      new error displaced an existing one. Adding the module to the gate is
      out of scope — those 11 errors are a separate defect (see below).
- [x] This PR flips A-031 to `status: fixed` in `.trellis/audit/ledger.md`,
      in the same PR as the fix — this epic's convention, inherited from the
      parent PRD; the ledger itself states no such rule, its A-031 entry
      (`ledger.md:333`) carrying only status, evidence, why, and fix. Its
      evidence line is rewritten to cite
      **symbol names**, not line numbers: the extraction moves both methods,
      so any number written today — including the corrected `:604`/`:854` —
      is stale on merge. Line-number drift is what made the original
      `:442`/`:691` evidence unusable; do not reproduce the defect while
      closing it.

## Known hazard: `list` is shadowed inside `CommandTraceStore`

The class defines a `list()` method, so inside its body the name `list` no
longer resolves to the builtin. That accounts for 10 of the 11 strict-mypy
errors (the 11th is an unrelated `no-any-return` at `server_traces.py:687`).
`server_traces.py:854` reports `Function "…CommandTraceStore.list" is not
valid as a type` for `_replace_sqlite_traces(self, traces: list[CommandTrace])`,
the exact signature this task edits.

Consequence for this task: the new helper must not introduce any `list[...]`
annotation inside the class body. The shipped signature
(`conn: sqlite3.Connection`, `trace: CommandTrace`, `payload: dict[str, Any]`,
`delete_fts_first: bool`) avoids it — `dict` is not shadowed, only `list`. Do
not "fix" the shadowing here — renaming a public store method is a separate,
wider change.

## Out of scope

- A-032 / A-033 / A-037 — the two sibling children.
- Any change to the FTS schema, the retention policy, or the trace payload.
- Renaming `CommandTraceStore.list` or adding `server_traces.py` to the mypy
  clean gate — recorded as a follow-up, not done here.

## Verification results (2026-08-06, HEAD 17c5e2f)

Every criterion above ran; measured output:

- `grep -c "INSERT OR REPLACE INTO command_traces"` → **1** (was 2);
  `grep -c "INSERT INTO command_traces_fts("` → **2** (was 3). The remaining
  FTS insert is the count-mismatch rebuild inside `_ensure_sqlite_fts`.
- `.venv/bin/pytest tests/test_server.py -k "sqlite or fts" -n 0` →
  `19 passed, 103 deselected`. Full suite: `1803 passed, 2 skipped in 237.17s`.
- `pre-commit run --all-files` → every hook `Passed`. `check_mypy_gate.py` →
  clean at 34 modules. `git diff --check` → clean.
- Normalized `mypy --strict` diagnostics on `server_traces.py`: `diff` against
  the pre-change baseline → **no output**. The 11 pre-existing errors are
  byte-identical after normalization; none added, none displaced.
- **Behavior identity:** a scratch oracle drove both write paths (`record()` →
  `_insert_sqlite` including an id-overwrite, and `import_payload()` →
  `_replace_sqlite_traces`) over a 4-trace corpus with unicode, embedded
  quotes, tabs/newlines, multi-scenario ordering, and an empty scenario tuple,
  then dumped every raw column of `command_traces` and `command_traces_fts`.
  Pre- vs post-change: `IDENTICAL (16 rows, fts=True)`.
- **Tests have teeth:** inverting `delete_fts_first` to `False` on the insert
  path makes
  `test_command_trace_sqlite_record_replaces_rather_than_duplicates_fts_row`
  fail with `2 = len([...trace_id 3 twice...])`. Reverted; suite re-verified
  green and the oracle re-checked `IDENTICAL`.
- `server_traces.py`: 1,025 → **998** lines.

Not verified here (out of scope): CI on the PR, and remote review.
