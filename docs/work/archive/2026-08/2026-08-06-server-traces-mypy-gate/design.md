# Design — Unblock server_traces.py for the mypy clean gate

## 1. Scope / Trigger

Cross-layer contract change: a public method on `CommandTraceStore` is renamed,
and `CommandTrace.to_dict()`'s return type — the shape persisted to SQLite and
served over HTTP — becomes a `TypedDict`. Code-spec depth applies.

Measured on `main` at `7c815bd`:

```
$ .venv/bin/mypy --strict src/anomaly_metric_creator/server_traces.py
Found 11 errors in 1 file (checked 1 source file)
$ .venv/bin/python tools/check_mypy_gate.py --list | wc -l
34
```

## 2. Decision: rename the method

The PRD left two shapes open and required a call-site audit first. The audit
is done and it inverts the PRD's risk assessment.

**The PRD feared** that `legacy.py` re-imports, `state.legacy` lookups, the
debug UI, and the MCP surfaces might all reach `CommandTraceStore.list`.

**None of them do.** Enumerated from the tree, not from memory:

| Surface | Reaches it? | Evidence |
| --- | --- | --- |
| `legacy.py` re-export | no | no `CommandTraceStore` / `trace_store` reference in the file |
| `server_mcp.py` | no | no `traces`-adjacent `list` reference |
| `server_debug_ui.py` | no | same |
| `docs/`, `README.md` | no | same |
| `src/` | **1 call site** | `server.py:664` |
| `tests/` | 19 call sites | 5 files |
| dynamic dispatch | none | no `getattr` on the store, no `"list"` string key |

**One correction to that audit.** `CommandTraceStore` *is* re-exported as public
API — `server.py:39`, `CommandTraceStore as CommandTraceStore,  # noqa: F401` —
so "zero public surface" is wrong as stated. What the audit establishes is
narrower and still sufficient: no *caller* outside this repository's own code
is reachable from the tree, and no in-repo consumer of the export uses `.list`.

Renaming is therefore a real break for a hypothetical external
`anomaly_metric_creator.server.CommandTraceStore().list()`. Accept it
knowingly: this package is a synthetic-artifact generator and incident
simulator, not a consumed library, its facade-export posture is itself an open
question (task `07-06-library-api-error-posture`), and no release notes or
README document the trace store's method surface. If that posture changes
later, this rename is one line in a migration note.

`test_server_architecture_cleanup_modules_back_public_facade`
(`test_server.py:3256`) pins that `server` re-exports these modules' names. It
compares module and class identity, not method names, so the rename does not
disturb it — but the PRD's acceptance criterion about the re-import surface
should be read as *the export still resolves*, which it does.

One class, one `def list` (`server_traces.py:269`). The `memory.list` /
`sqlite.list` pair in `test_sim_mutation_correctness.py` are two *instances* of
that same class — memory-backed and SQLite-backed — not sibling
implementations, so there is one definition to rename.

**Blast radius: 21 mechanical edits, entirely in-repo, no `legacy.py` surface
and no in-repo consumer of the public re-export that touches `.list`.** (Not
"zero public surface" — the re-export above is public; the point is that
nothing in the tree calls through it.) That is small enough that option 2's cost decides it: keeping the
name obliges us to ship a new mechanical lint policing bare `list[...]`
annotations inside the class *forever*, to guard a trap we could simply
remove. Renaming makes that lint unnecessary.

New name: **`list_traces`**. Explicit, greppable, and it is the name the PRD
already floated, so it needs no further bikeshedding.

### Why the shadowing bites at all

Worth recording, because it is counter-intuitive and the next reader will
wonder why `list(self._items)` on line 280 works fine:

Method bodies resolve names by LEGB — class-body bindings are *not* in a
method's scope — so at runtime `list(...)` inside a method is the builtin.
Annotations are different: mypy resolves them in the enclosing **class** scope,
where `list` is the method object. That is why all ten shadowing errors sit at
annotation sites and their derived `attr-defined` consequences, and why the
module runs correctly today despite them.

`CommandTraceStore` spans lines 131–904 — the only two classes are
`CommandTrace` (38) and `CommandTraceStore` (131), and module-level functions
resume at 907 — so every annotation in that 774-line class body is inside the
shadow. The module-level returns at 926 and 962 are outside it, which is one
reason they are not conversion candidates.

## 3. Signatures

### Renamed method

```python
# before
def list(self, limit: int | None = None) -> list[dict[str, Any]]: ...
# after
def list_traces(self, limit: int | None = None) -> list[TraceListItem]: ...
```

Semantics unchanged: newest-first, `limit=None` means all, a negative limit is
clamped to 0 (audit A-017), `limit == 0` returns `[]` on both backends.

### Payload TypedDicts

`to_dict()` returns 24 keys. The listing path returns those 24 keys **plus**
`version`:

```python
return [{"version": version, **trace.to_dict()} for trace in reversed(items)]
```

So one `TypedDict` cannot serve both. Two, by inheritance:

```python
class TracePayload(TypedDict):
    # Required (13): `from_dict` reaches these through `payload[key]` -- either
    # directly or via `_trace_int_field` -- so a row missing one is already a
    # KeyError today.
    id: int
    received_at_wall_time: str
    simulated_time: str
    raw_input: str
    client: str
    command_family: str
    verb: str | None
    resource_kind: str | None
    resource_name: str | None
    namespace: str | None
    support_status: str
    matched_rule_id: str | None
    exit_code: int
    # NotRequired (11): `from_dict` defaults these, so an older persisted row
    # may legitimately omit them. See the boundary section below.
    argv: NotRequired[list[str]]
    active_scenarios: NotRequired[list[str]]
    parsed_flags: NotRequired[dict[str, Any]]
    stdout_preview: NotRequired[str]
    stderr_preview: NotRequired[str]
    stdout: NotRequired[str]
    stderr: NotRequired[str]
    latency_ms: NotRequired[float]
    fingerprint: NotRequired[str]
    guessed_intent: NotRequired[str]
    request_id: NotRequired[str]

class TraceListItem(TracePayload):
    version: int
```

Exact member types are read off the `CommandTrace` dataclass fields during
implementation, not guessed from this sketch; the sketch fixes the *shape*
(24 + 1) and the inheritance direction.

Both `TypedDict`s are **module-level**, not nested in `CommandTraceStore`.
Nested, `argv: list[str]` would hit the very shadow this task removes.

`TraceListItem` inheriting `TracePayload` lets the spread literal check without
a cast, because the constructed dict is a `TraceListItem` by structure.
Confirmed against the real checker rather than assumed — a scratch module
under `mypy --strict` accepts both

```python
return [{"version": version, **to_dict()}]              # spread literal
return [TraceListItem(version=version, **to_dict())]    # constructor
```

with `Success: no issues found in 1 source file`. Prefer the spread, which is
what the code already writes.

### Which annotations convert, and which must not

The module has 16 `dict[str, Any]` return annotations, plus one `dict[str,
Any]` *parameter* annotation on `_insert_trace_row`. Nine of those 17 sites
carry the trace payload — eight of the returns and that one parameter. The
other eight returns are summaries and export envelopes that look identical and
must be left alone. Getting this boundary wrong is the main
correctness risk of the payload work, so it is enumerated rather than
described:

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

`_list_sqlite` is on the list because it is not a payload producer despite the
name: its final expression is
`[{"version": version, **self._row_to_payload(row)} for row in rows]`, the same
listing shape `list_traces` returns on the memory path. `_export_*`, by
contrast, return bare `to_dict()` output with no `version`.

**Left as `dict[str, Any]`** — different shapes, no change: `unsupported_summary`
(331), 395, `export_payload` (434), `import_payload` (444), 759, 830,
`_unsupported_summary_from_traces` (926), `unsupported_summary_from_traces`
(962).

## 4. Contracts

**Wire contract is unchanged.** `server.py:664` serves
`{"items": state.traces.list_traces(limit=limit)}`; the method name is not
wire-visible, the key names are, and none of them move. No HTTP response, MCP
payload, debug-UI field, or persisted column changes. `schema.json` is
untouched.

**Persistence contract is unchanged.** The 21-column INSERT keeps its columns
and order; only the static type of the `payload` argument narrows.

## 5. Validation & Error Matrix

| Condition | Behavior |
| --- | --- |
| `limit=None` | all traces, newest first — unchanged |
| `limit < 0` | clamped to 0, both backends — unchanged (A-017) |
| `limit == 0` | `[]`, both backends — unchanged |
| `payload_json` row is not a JSON object | see below |
| a caller still uses `.list(` | `AttributeError` at runtime, caught by the suite |

### The `_row_to_payload` boundary — the stored shape is not the written shape

`_row_to_payload` is `json.loads(row["payload_json"])`, which is the 11th mypy
error (`no-any-return` at `:717`).

The tempting justification — "this store wrote the row, so a total
`TracePayload` cast is safe" — is **wrong**, and the code says so. The store
persists the whole `to_dict` blob and reloads it through `from_dict` across a
process restart, and `from_dict` deliberately tolerates **eleven** absent keys:

```python
parsed_flags=dict(payload.get("parsed_flags", {})),
stdout_preview=payload.get("stdout_preview", ""),
stderr_preview=payload.get("stderr_preview", ""),
stdout=payload.get("stdout", ""),
stderr=payload.get("stderr", ""),
latency_ms=float(payload.get("latency_ms", 0.0)),
fingerprint=payload.get("fingerprint", ""),
guessed_intent=payload.get("guessed_intent", ""),
request_id=payload.get("request_id", ""),
```

Two more hide behind a helper and are easy to miss by grepping for `.get(`:
`argv` and `active_scenarios` route through `_trace_tuple_field`, whose first
line is `value = payload.get(key, ())`. `id` and `exit_code` route through
`_trace_int_field`, which subscripts — so those two stay required.

`request_id`'s field comment states the intent outright: "any older payload
without the key construct a trace unchanged… It still survives a SQLite restart
because the store persists the whole `to_dict` blob." A row written by an older
build is a supported input, not corruption. A total `TypedDict` would declare
those eleven required and a cast would assert a shape the reader is explicitly
built to tolerate the absence of.

**Decision: `TracePayload` marks those eleven `NotRequired`.** The writing side
is unaffected — `to_dict` always emits all 24, and a `NotRequired` key may be
written — while the reading side gets a type that admits legacy rows honestly.
The other 13 keys stay required; `from_dict` subscripts them, so a row missing
one is already a `KeyError` today and the type should say so.

`_row_to_payload` then closes with `cast(TracePayload, ...)` guarded on the
decoded value being a `dict`, which converts a genuinely malformed row into a
clear failure at the read instead of an `AttributeError` three frames later.
Comment the cast with the reasoning; a bare `cast` reads as an oversight.

This is narrower than the reader-side validation CLAUDE.md requires for
`--instance-config` and `schema.json`: those are user-authored, this is
machine-written and only ever *older*, and per-field validation on every row of
a listing would be a real cost for no reachable failure. Full validation
belongs to the trace-export hardening task (`07-17-audit-trace-export-hardening`)
if it is wanted at all.

## 6. Good / Base / Bad Cases

- **Good** — `mypy --strict` reports 0 errors; `server_traces.py` is in
  `CLEAN_MODULES`; the full suite is green. The only intended behavior delta is
  the new `TypeError` on a `payload_json` row that decodes to a non-object,
  which is unreachable from any store-written row; every other path is
  byte-for-byte unchanged.
- **Base** — a rename-only change with the `TypedDict` work deferred still
  clears 10 of 11 errors but cannot enter `CLEAN_MODULES`, so it is not a
  shippable stopping point. The task is one PR.
- **Bad** — a missed call site. `.list(` is a common enough spelling that a
  blind global replace would hit unrelated objects; see the implement plan's
  scoping rule.

## 7. Tests Required

No new behavior, so the existing suite is the regression net — 19 test call
sites already exercise both backends. Specifically:

| Assertion point | File |
| --- | --- |
| ordering and limit on both backends | `test_sim_mutation_correctness.py:314-325` |
| listing after SQLite restore | `test_server.py:2124`, `:2165` |
| `version` key present in listing items | `test_server.py:2179` |
| listing via the HTTP endpoint | `test_server_watch.py` (3 sites) |
| MCP-side listing | `test_server_mcp.py:686`, `:716` |

Add one test: **the old name is gone.** `assert not hasattr(store, "list")`
— cheap, and it catches a half-finished rename that leaves a compatibility
alias nobody meant to keep.

Do **not** add a `list = list_traces` alias. It would reintroduce the exact
class-body binding this task removes, and mypy would fail again.

## 8. Wrong vs Correct

### Wrong

```python
class CommandTraceStore:
    def list(self, limit: int | None = None) -> list[dict[str, Any]]:
        ...
```

`list` in the class body shadows the builtin for every annotation in the
774-line class body below it. Ten errors, all remote from their cause.

### Correct

```python
class CommandTraceStore:
    def list_traces(self, limit: int | None = None) -> list[TraceListItem]:
        ...
```

## 9. Interaction with the module-size ratchet

`server_traces.py` is enrolled in `tools/check_module_size.py` at ceiling
**1013** — its exact size, no headroom. The rename is line-neutral, but the
`TypedDict` block is roughly 30 new lines and the `TypedDict` import is one
more.

Per the ratchet's documented rule, that growth is **not separable** — a
`TypedDict` describing this module's payload belongs beside it — so the
sanctioned remedy is to raise the ceiling in the same diff. Expect
`RATCHET["server_traces.py"]` to move from 1013 to roughly 1045, with the
final number read off the tree, not estimated. Say so in the PR body; a
ceiling bump is meant to be a line a reviewer sees.

## 10. Rollback

One squash commit, no data migration, no persisted-format change. `git revert`
restores the old method name and the `dict[str, Any]` annotations. Any branch
written against `list_traces` in between would need the rename reapplied —
there are none planned.
