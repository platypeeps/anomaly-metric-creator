# Design — `server.py` alias block via module `__getattr__`

## 1. Scope / Trigger

Cross-layer contract change: it alters how the public
`anomaly_metric_creator.server` attribute surface is produced. The published
names do not change; the mechanism producing them does, so code-spec depth
applies.

## 2. Signatures

New module-level surface in `src/anomaly_metric_creator/server.py`:

```python
def __getattr__(name: str) -> Any: ...
def __dir__() -> list[str]: ...
```

Removed: 187 of the 227 `NAME = _server_ops.NAME` statements at
[server.py:309-535](src/anomaly_metric_creator/server.py:309).

Added: one `from .server_ops import (...)` block binding the 40 names that
have a real consumer.

## 3. Contracts

`__getattr__(name)`

| | |
| --- | --- |
| Input | `name: str` — an attribute not found in `server`'s module globals |
| Returns | `getattr(_server_ops, name)` when `name` is not dunder-shaped and `server_ops` defines it |
| Raises | `AttributeError(f"module {__name__!r} has no attribute {name!r}")` otherwise |
| Env keys | none |

### The dunder guard is load-bearing, not defensive padding

`server.py` defines no `__all__`; `server_ops` defines one with 227 entries.
An unguarded delegation would therefore make `server.__all__` start resolving
— silently changing `from anomaly_metric_creator.server import *` from
"every public global in `server.py`" to "the 227 ops names", and changing
`hasattr(server, "__all__")` from `False` to `True`. Nothing in-repo does a
star-import of `server`, so this is a latent contract change rather than a
live break, which is precisely why it has to be closed in the same edit that
introduces the seam: a leak nobody trips over is a leak nobody removes.

`__getattr__` therefore refuses any `__dunder__` name outright and lets the
normal `AttributeError` stand. Machinery that probes modules for optional
dunders (`copy`, `pickle`, `pytest`'s collection, `inspect`) then sees the
same answer it saw before this change.

`__dir__()` returns `globals()` unioned with the delegated half of
`dir(_server_ops)`, so the delegated names stay visible to `dir()`, `inspect`,
and REPL completion. The delegated half is filtered through the *same*
predicate the guard uses (`_is_delegation_excluded`), because a raw union
carries `server_ops.__all__` into `dir(server)` while `server.__all__` raises —
`dir()` would advertise an attribute that reading refuses. Sharing one
predicate between the guard and the listing is what keeps the two from
drifting.

### Why the 40 cannot be delegated

PEP 562's module `__getattr__` is consulted for attribute access **on the
module object**. It is not consulted for global-name resolution inside the
module's own code. `server.py` reads 30 of these names as bare globals — e.g.
`_is_kubernetes_api_path` at [server.py:932](src/anomaly_metric_creator/server.py:932),
`_k8s_status_response` at [server.py:956](src/anomaly_metric_creator/server.py:956).
Deleting those assignments produces a `NameError` on a request path, not an
`ImportError` at startup. A further 24 are read elsewhere in-repo as
`server.<name>`; those *would* survive delegation, but binding them
explicitly keeps `is`-identity and monkeypatch semantics byte-identical to
today rather than "identical as far as we checked". Union: 40.

### Why delegation is permissive, not a 187-name tuple

An explicit tuple would relocate the lockstep rather than remove it, and
removing it is the entire point — the block went from 40+ names to 227 by
being hand-maintained. `__getattr__` therefore forwards **any** name
`server_ops` defines.

Consequence, stated rather than discovered later: `server` begins publishing
7 names it did not publish before (`_exposed_active_scenarios`,
`_exposed_component_scenarios`, `_is_deployment_rollout_target`,
`_render_rollout_pause`, `_render_rollout_resume`, `_render_rollout_undo`,
`_rollout_component` — present in `server_ops.__all__` but never aliased).
This is not new exposure: `server._server_ops` already reaches the whole
module, and the eval-mode ground-truth wall is an HTTP-surface rule, not a
Python-attribute rule. A repo grep found no guarded `getattr(server, x,
default)` that would change branch as a result.

Note for the reader who reaches for `__all__` as the source of truth: it is
not one here. `server_ops.__all__` and the alias block are both 227 entries
but differ by 7 in each direction — 7 aliased names (`_capture_traceback_tail`,
`_emit_error_record`, `_record_server_error`, `k8s_watch_object_key`,
`k8s_watch_objects`, `k8s_watch_plan`, `k8s_watch_trace_response`) are absent
from `__all__`. Delegation keys off `hasattr(_server_ops, name)`, which covers
both sets.

### What the block was hiding (found by the pre/post surface diff)

`server.py:43-44` defined its own `DEFAULT_RELEASE = "simulated-saas"` and
`DEFAULT_CHART = "simulated-saas-0.3.0"` literals — duplicates of the
`server_ops` constants, and a violation of the one-registry-per-fact rule. They
were invisible because the alias block reassigned both a few hundred lines
later, so the duplicate values never won.

Delegation removes that accidental correction: `DEFAULT_CHART` has no in-repo
`server.<name>` reader, so it would have fallen through to the stale local
literal while `DEFAULT_RELEASE` (explicit) kept tracking `server_ops`. Values
are equal today, so nothing observable changed — but the next edit to
`server_ops.DEFAULT_CHART` would have silently desynchronized them.

Both literals are therefore deleted, with a comment at their old position
recording that the constants are owned by `server_ops`. Neither name has a
load site in `server.py`, so nothing reads them before the compatibility block
binds them. This is the concrete payoff of running the surface diff as a real
check rather than trusting a green suite: the suite was green either way.

## 4. Validation & Error Matrix

| Condition | Result |
| --- | --- |
| `name` is one of the 40 explicit binds | resolved from module globals; `__getattr__` never runs |
| `name` is any other `server_ops` attribute | `getattr(_server_ops, name)`, same object identity |
| `name` is unknown to both | `AttributeError`, standard module message shape |
| `name` is `__dunder__`-shaped | `AttributeError` without consulting `server_ops` — see the dunder guard above; `server.__all__` must keep *not* existing |
| test sets `server.<name>` | real module global written; shadows `__getattr__` thereafter, as it shadowed the assignment before |
| test sets `server._server_ops.<name>` | delegated names follow the new value; the 40 explicit binds do not — unchanged from today's snapshot semantics |

## 5. Good / Base / Bad Cases

- **Good** — `server.render_command` (delegated): resolves to
  `server_ops.render_command`, same object.
- **Base** — `server.build_state` (explicit): unchanged bind, unchanged
  identity, unchanged monkeypatch behavior.
- **Bad** — `server.no_such_name`: `AttributeError`, and `hasattr` is `False`.

## 6. Tests Required

New file `tests/test_server_alias_surface.py`:

1. `test_every_server_ops_name_resolves_through_server` — derives the name
   list from `dir(server_ops)` at runtime (never a stored list, per the
   one-registry-per-fact rule) and asserts
   `getattr(server, n) is getattr(server_ops, n)` for each.
   The historic 227 are a subset of `dir(server_ops)` — including the 7 that
   `server_ops.__all__` omits, which are module attributes regardless — so
   this test subsumes a frozen historic list. Proving the 227 specifically is
   a one-time migration check (implement.md check 2) driven off the pre-change
   blob in git; it is deliberately **not** a permanent test, because a test
   that shells out to `git show <base>` binds the suite to clone depth and to
   a base commit that stops existing.
2. `test_unknown_attribute_raises_attribute_error` — `AttributeError`, with
   the module name in the message.
3. `test_dunder_names_are_not_delegated` — `hasattr(server, "__all__")` is
   `False` even though `server_ops.__all__` exists. This is the star-import
   contract change the dunder guard exists to prevent, so it gets its own
   test rather than riding along in the unknown-attribute case.
4. `test_dir_includes_delegated_and_explicit_names` — a delegated name and an
   explicit one both appear in `dir(server)`.
4b. `test_dir_lists_nothing_the_dunder_guard_refuses` — the converse, and the
   one the raw union failed: `__all__` is absent from `dir(server)`, and every
   listed name is `hasattr`-readable. Added in the PR #377 review round.
5. `test_explicit_binds_cover_every_internal_use` — the enumeration-proof
   guard: AST-scan `server.py` for bare-global loads of names that
   `server_ops` defines, and assert each is explicitly bound. This is the
   test that fails if a future edit adds an internal use of a delegated name,
   which is the `NameError`-at-request-time failure mode above.

Assertion points: object identity (`is`), not equality; `AttributeError` type
and message; membership in `dir()`.

## 7. Wrong vs Correct

### Wrong

```python
# Delegates everything, including names server.py itself reads as globals.
def __getattr__(name: str) -> Any:
    return getattr(_server_ops, name)
# ...and 900 lines later, inside a request handler:
if _is_kubernetes_api_path(path):   # NameError: module __getattr__ not consulted
```

### Correct

```python
# The 40 names with a real consumer stay real module globals. `__getattr__`
# is consulted for module *attribute* access only (PEP 562) and never for
# global-name resolution inside this module, so anything server.py reads as a
# bare global must be bound here.
from .server_ops import (
    build_state as build_state,
    _is_kubernetes_api_path as _is_kubernetes_api_path,
    ...
)


def __getattr__(name: str) -> Any:
    """Forward the historic `server.<ops name>` surface to `server_ops`.

    Dunders are refused before the forward: `server_ops` defines `__all__`
    and this module does not, so an unguarded delegation would quietly give
    `server` a star-import list it never had.
    """
    if name.startswith("__") and name.endswith("__"):
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    try:
        return getattr(_server_ops, name)
    except AttributeError:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}"
        ) from None
```

## Compatibility inventory

| Surface | Crosses this seam? | Action |
| --- | --- | --- |
| `server_commands.py`, `server_kubernetes.py`, `server_helm.py` | no — import from `server_ops` directly | none |
| `server_mcp.py` | no | none |
| `cli_subcommands.py:145` | imports `serve_main`, defined in `server.py` | none |
| `legacy.py` / `state.legacy` lookups | do not read `server.<ops name>` | none |
| `tests/` | 24 names read as `server.<name>` | all in the explicit-40 |

## Rollout / rollback

Single reversible commit; rollback is restoring the assignment block. No data,
no persisted format, no HTTP-surface change.

## Module-size interaction

`server.py` is enrolled in `tools/check_module_size.py`'s ratchet at exactly
2,208. All 227 assignment lines go; an explicit import block for 40 names
plus `__getattr__`, `__dir__`, and the comment explaining the split come back.
**Measured after the change: 2,078** (−130, including the review-round
`__dir__` filter). Measure, do not estimate, when
writing the ceiling.

The lint will **not** catch a stale-high ceiling. Reading
`tools/check_module_size.py`: an enrolled module violates only when it exceeds
its ceiling, or when it drops to or under the 800-line cap (the stale-entry
rule). `server.py` at 2,078 under a 2,208 ceiling is silently clean. So
"lower the ceiling" is a review-gate obligation verified by reading the diff,
not something a green lint run attests to — and leaving it high would
silently re-authorize the 144 lines this task removed.
