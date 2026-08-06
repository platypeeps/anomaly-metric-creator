# Unblock server_traces.py for the mypy clean gate

## Goal

Get `src/anomaly_metric_creator/server_traces.py` into
`tools/check_mypy_gate.py`'s `CLEAN_MODULES` list (34 modules today), so the
trace store is type-checked in CI like the rest of the package.

## Why it is blocked now

`CommandTraceStore` defines a `list()` method — the public store listing API.
Inside the class body that name shadows the builtin, so any `list[...]`
annotation on a method of that class resolves to the method object instead of
the generic alias and fails to type-check.

Measured on `main` at `c6f81df` (after PR #345 merged):

```
$ .venv/bin/mypy --strict src/anomaly_metric_creator/server_traces.py
Found 11 errors in 1 file (checked 1 source file)
```

**10** of the 11 are this shadowing — they surface as
`Function "…CommandTraceStore.list" is not valid as a type  [valid-type]` and
`"list?[…CommandTrace]" has no attribute "__iter__"  [attr-defined]`. The 11th
is unrelated:

```
server_traces.py:717: error: Returning Any from function declared to
return "dict[str, Any]"  [no-any-return]
```

So the shadowing is essentially the whole gap. `python3 tools/check_mypy_gate.py
--list` currently prints **34** modules and `server_traces.py` is not among
them.

The hazard is currently documented in the `CommandTraceStore` class docstring
("Annotation hazard: `list` shadows the builtin throughout this class body…").
That is prose, and this repo prefers a mechanical `tools/check_*.py` lint over
a prose rule whenever the pattern is greppable — which this one is.

## Requirements

- Eliminate the 10 shadowing errors. Two candidate shapes, to be decided in
  design:
  1. Rename the store method (`list` → e.g. `list_traces`) and update every
     caller. This is a **public surface change**: `legacy.py` re-imports,
     `state.legacy` lookups in the server, the debug UI, and the MCP surfaces
     may all reach it. Requires a call-site audit before it is chosen.
  2. Keep the method name and rewrite the in-class annotations to avoid the
     bare builtin — `Sequence[...]`, `typing.List[...]`, or quoted
     annotations. Non-breaking, but leaves the trap for future edits.
- Resolve the remaining `no-any-return` at `server_traces.py:717`.
- Add `server_traces` to `CLEAN_MODULES` in `tools/check_mypy_gate.py` in the
  same change, so the gate enforces the result.
- If option 2 is chosen, add a mechanical lint (with tests, per repo
  convention) rejecting a bare `list[...]` annotation inside
  `CommandTraceStore`, and drop the prose note in favour of pointing at it.

## Also in scope: the payload TypedDict

Raised in the PR #345 local review and deferred there as scope expansion.
`_insert_trace_row` takes `payload: dict[str, Any]`, which is the shape
`CommandTrace.to_dict()` returns. A `TypedDict` would let mypy check the
21-column INSERT's inputs instead of accepting `Any`.

It belongs here rather than in its own task: same file, same gate, and
touching `to_dict` and its consumers is exactly what surfaces the rest of the
module's typing debt.

## Acceptance Criteria

- [ ] `mypy --strict src/anomaly_metric_creator/server_traces.py` reports 0 errors.
- [ ] `server_traces` appears in `CLEAN_MODULES` and `tools/check_mypy_gate.py`
      exits 0.
- [ ] Full suite green; no behavior change to the trace store.
- [ ] If the store method was renamed, every call site is updated and the
      `legacy.py` re-import surface is unchanged for existing importers.
- [ ] If the method name was kept, the mechanical lint exists, has tests, and
      fails on a bare `list[...]` annotation added inside `CommandTraceStore`.

## Out of scope

Any behavior change to trace persistence, search, or the FTS mirror. This is a
typing task.

## Source

PR #345 (audit A-031), recorded as an explicit follow-up in its description and
in the local-review disposition comment.
