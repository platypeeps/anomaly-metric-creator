# Implement — Unblock server_traces.py for the mypy clean gate

One PR. The rename alone clears 10 of 11 errors but cannot enter
`CLEAN_MODULES`, so there is no shippable intermediate stop.

Baseline to reproduce before starting:

```bash
.venv/bin/mypy --strict src/anomaly_metric_creator/server_traces.py
```

Expect `Found 11 errors in 1 file (checked 1 source file)`. If the count has
moved, stop and reconcile the design against the tree before editing.

## Step 1 — Rename the method

- [x] `server_traces.py:269`: `def list(` → `def list_traces(`. Signature and
      body otherwise untouched, including the A-017 negative-limit clamp and
      its comment.
- [x] Update all 20 call sites across 6 files.

Enumerate them; do not blind-replace. `.list(` also matches unrelated objects,
and this grep is the scoping rule:

```bash
grep -rn "\.list(" src/ tests/ --include=*.py
```

That prints **20** lines: 1 in `src/anomaly_metric_creator/server.py`, and 19
across `tests/test_server.py`, `tests/test_server_watch.py`,
`tests/test_server_mcp.py`, `tests/test_server_ops_fuzz.py`, and
`tests/test_sim_mutation_correctness.py`. Every one is a trace-store call —
verified during the design audit — so all 20 change, and the same grep must
print nothing afterward.

`server_traces.py` itself contributes zero of those 20: the definition is
`def list(`, which `\.list(` does not match, and the module makes no internal
`.list(` call. Edit line 269 by hand in this step.

**Gate:**

```bash
.venv/bin/mypy --strict src/anomaly_metric_creator/server_traces.py
```

Expect exactly one remaining error, the `no-any-return` at `_row_to_payload`.
Ten shadowing errors gone. If any `valid-type` error survives, a `list`
binding is still in the class body.

```bash
.venv/bin/pytest tests/test_server.py tests/test_server_watch.py \
  tests/test_server_mcp.py tests/test_server_ops_fuzz.py \
  tests/test_sim_mutation_correctness.py -q
```

Green here means no call site was missed. **Rollback point:** this step is
self-contained; revert it alone if the payload work goes sideways.

## Step 2 — Add the payload TypedDicts

- [x] Define `TracePayload` (24 keys) and `TraceListItem(TracePayload)` adding
      `version: int`, both **module-level**. Read each member type off the
      `CommandTrace` dataclass fields — do not copy the design sketch's types
      on faith. Nesting them inside `CommandTraceStore` would put `argv:
      list[str]` back under the shadow this task just removed.
- [x] 13 keys required, 11 `NotRequired` — the split is in the design and is
      derived from what `from_dict` subscripts versus defaults. Do not make all
      24 required: a persisted row written by an older build legitimately omits
      the 11, which is why `from_dict` defaults them.
- [x] Convert the nine payload-shaped annotations, and only those nine. The
      design's § "Which annotations convert, and which must not" is the
      authority; there are eight more `dict[str, Any]` returns in the file that
      look identical and are summaries or export envelopes:

| Line | Member | New type |
| --- | --- | --- |
| 102 | `CommandTrace.to_dict` | `TracePayload` |
| 269 | `list_traces` | `list[TraceListItem]` |
| 289 | `get` | `TracePayload \| None` |
| 482 | `_export_memory_traces` | `list[TracePayload]` |
| 486 | `_export_sqlite_traces` | `list[TracePayload]` |
| 609 | `_insert_trace_row` (`payload` param) | `TracePayload` |
| 716 | `_row_to_payload` | `TracePayload` |
| 724 | `_list_sqlite` | `list[TraceListItem]` |
| 740 | `_get_sqlite` | `TracePayload \| None` |

Line numbers are pre-edit and will drift as you work; match on the member
name, not the number.

**Gate:** mypy must not report a `typeddict-item` or `typeddict-unknown-key`
error. If it does, `to_dict` and the `TypedDict` disagree on a key name or
type — fix the `TypedDict` to match the code, never the reverse; this task
changes no behavior.

## Step 3 — Close `_row_to_payload`

- [x] Guard that `json.loads(...)` returned a `dict`, raise `TypeError` if not,
      then `cast(TracePayload, ...)`.

The message must **not** name the row id. `_row_to_payload` has five call sites
(`:491`, `:738`, `:748`, `:810`, `:857`) and every query feeding them selects
`payload_json` alone — `server_traces.py:489`, `:730`, `:743`, `:792`, `:850` —
so `row["id"]` would itself raise inside the error path. Adding `id` to five
SELECTs to improve one error string is scope this task does not carry.

The guard governs `_row_to_payload`'s five callers and nothing else. Two other
persisted-row reads decode `payload_json` themselves and bypass the helper:
`unsupported_summary` (`:356` selecting, `:363` decoding) and
`_load_sqlite_tail` (`:593`–`:600`). Both feed `CommandTrace.from_dict`, which
fails on its own terms for a non-object row. Widening the guard to cover them
is the trace-export hardening task's work, not this one's.

- [x] Comment the cast with why full field validation is not done here: the row
      is machine-written by this same store and only ever *older*, unlike the
      user-authored `--instance-config` and `schema.json` boundaries.
- [x] Add a test that a row whose `payload_json` decodes to a non-object raises
      `TypeError` from the read path, not an `AttributeError` further downstream.
      Write the bad value straight into the SQLite file the store opened; there
      is no store API that produces one.

**Gate:**

```bash
.venv/bin/mypy --strict src/anomaly_metric_creator/server_traces.py
```

Expect `Success: no issues found in 1 source file`.

## Step 4 — Enter the gate

- [x] Add `"src/anomaly_metric_creator/server_traces.py"` to `CLEAN_MODULES` in
      `tools/check_mypy_gate.py`, in sorted position — between
      `server_ops_support.py` and `timeutil.py`.
- [x] Update `tests/test_mypy_gate_lint.py:30`, `assert len(modules) == 34` →
      `== 35`, in the same step. The tuple's length is asserted exactly, so
      Step 7's suite cannot pass without it. Its two neighbouring assertions
      still hold: `modules[0]` stays `__init__.py` and `modules[-1]` stays
      `timeutil.py`, because `server_traces.py` sorts before `timeutil.py`.

**Gate:**

```bash
.venv/bin/python tools/check_mypy_gate.py
```

Expect exit 0. Then confirm the module is listed and the count moved 34 → 35:

```bash
.venv/bin/python tools/check_mypy_gate.py --list | grep -c .
.venv/bin/python tools/check_mypy_gate.py --list | grep server_traces
```

## Step 5 — Raise the module-size ceiling

`server_traces.py` is enrolled at exactly **1013**, so Step 2 will trip the
ratchet. This is the sanctioned non-separable case; see the design.

- [x] Read the new size, then set `RATCHET["server_traces.py"]` in
      `tools/check_module_size.py` to that exact number:

```bash
wc -l src/anomaly_metric_creator/server_traces.py
```

- [x] Update the entry's reason if it no longer reads true.

**Gate:**

```bash
.venv/bin/python tools/check_module_size.py
```

Expect exit 0. Do not pad the ceiling above the measured count.

## Step 6 — Lock the rename

- [x] Add a test asserting the old name is gone:
      `assert not hasattr(store, "list")`, beside the existing store tests in
      `tests/test_server.py`.

Do **not** add a `list = list_traces` alias. It reintroduces the class-body
binding this whole task removes and mypy fails again immediately.

## Step 7 — Full gates

```bash
.venv/bin/pytest
.venv/bin/pre-commit run --all-files
.venv/bin/ruff check tests/
git diff --check
```

The suite baseline is `1889 passed, 2 skipped`; expect `1891` after the two new
tests in Steps 3 and 6.

## Step 8 — Review readiness

- [x] Walk the 15 pre-PR checklist headings in
      `.trellis/spec/amc/backend/testing-quality.md` § Review Checklist.
- [x] Grep the **old** spellings repo-wide, per the doc-drift rule — the
      method name and the docstring's annotation-hazard note:

```bash
grep -rn "CommandTraceStore.list\b" . --include=*.md --include=*.py
grep -rn "Annotation hazard" src/ docs/ .trellis/
```

The class docstring's hazard note documents a trap that no longer exists after
this change. Remove it in the same diff — leaving it is exactly the
doc-vs-code drift this repo flags most.

- [x] PR body states the ceiling bump 1013 → *n* and why it was a bump rather
      than an extraction.

## Not doing

- No behavior change to trace persistence, search, or the FTS mirror — with
  one deliberate exception, Step 3's `TypeError` on a `payload_json` row that
  decodes to a non-object. It is unreachable from any store-written row.
- No new lint. That was option 2's obligation; the rename removes the trap it
  would have policed.
- No compatibility alias for `list`.
