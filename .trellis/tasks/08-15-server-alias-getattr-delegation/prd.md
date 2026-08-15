# Delegate the server.py ops alias block through module `__getattr__`

Child of `07-06-server-ops-decomposition` (step-6b precursor / adjacent-seam
settlement). Not an extraction step: it removes the lockstep tax every
remaining extraction step pays.

## Context

`server.py` carries a hand-maintained compatibility facade at
[server.py:309-535](src/anomaly_metric_creator/server.py:309) — **227**
`NAME = _server_ops.NAME` assignment lines, one per name the historic
`anomaly_metric_creator.server` import surface published before the ops
implementation moved into `server_ops.py`.

The block grows with the epic. It was 40+ names at the 2026-07-06 review and
is 227 today; every extraction step that publishes a new ops name has to
remember to append to it. The epic PRD records this as an adjacent seam to
settle "before the next extraction", and the epic's own follow-up list
already promoted `__getattr__` delegation from conditional to live:

> The original condition was "only if the manual block ever becomes a
> maintenance pain point" — it now holds.

`server.py` is also 2,208 lines, enrolled in `tools/check_module_size.py`'s
ratchet at exactly that ceiling, so the block is a standing obstacle to the
`server.py` split follow-up as well.

## Measured facts (2026-08-15, AST + repo grep, not recollection)

Of the 227 aliased names:

| Group | Count | Evidence |
| --- | ---: | --- |
| Referenced inside `server.py` itself as a bare global | 30 | AST `Name`/`Load` scan excluding each name's own assignment line |
| Read elsewhere in-repo as `server.<name>` | 24 | repo-wide grep over `src/`, `tests/`, `tools/` |
| Union — must stay a real module global | **40** | the two groups overlap by 14 |
| No in-repo consumer at all — pure historic surface | **187** | complement of the union |

The 187 are exactly what a module `__getattr__` is for: names that must keep
resolving for an outside importer, that nothing in this repository reads.

The 40 cannot be delegated. PEP 562's module `__getattr__` fires only for
attribute access **on the module object**; it does not participate in global
name resolution inside the module. Deleting `_is_kubernetes_api_path`'s
assignment while `server.py:932` still reads the bare global would be a
`NameError` at request time, not an import error — a failure the test suite
would surface, but the reasoning has to be recorded so a later reader does
not "simplify" the remaining 40 away.

## Goal

Replace the 187 consumer-less assignment lines with one typed module
`__getattr__` (plus `__dir__`) that delegates to `server_ops`, and bind the
40 load-bearing names through one explicit `from .server_ops import ...`
block. The published attribute surface of `anomaly_metric_creator.server` is
unchanged, name for name.

## Requirements

- `getattr(server, name)` resolves for **every one of the 227 names**, and to
  the identical object `server_ops` exposes.
- An unknown name raises `AttributeError` naming the module and the attribute
  — the standard message shape, not a silent `None`.
- `__dunder__`-shaped names are **not** delegated. `server_ops` defines
  `__all__` and `server.py` does not; forwarding it would silently change what
  `from anomaly_metric_creator.server import *` imports. `server.__all__` must
  keep not existing.
- `dir(server)` still lists the delegated names, so tab-completion and
  `inspect` do not regress.
- The 40 load-bearing names are bound explicitly, and the reason is a comment
  at the block, not tribal knowledge.
- Monkeypatching keeps working in both directions:
  - `monkeypatch.setattr(server, "<name>", ...)` writes a real module global
    that shadows `__getattr__`, exactly as it shadowed the assignment before;
  - `monkeypatch.setattr(server._server_ops, "<name>", ...)` keeps its current
    semantics — it does **not** retroactively change an already-bound explicit
    name, which is the pre-change behavior of a snapshot assignment and must
    not silently change for the 40.
- `server_ops.__all__` and the facades (`server_commands.py`,
  `server_kubernetes.py`, `server_helm.py`, `server_mcp.py`) are untouched:
  they import from `server_ops` directly and never crossed this seam.
- Behavior tests pass unchanged — no test edits beyond new coverage for the
  seam itself.

## Non-goals

- The `server.py` infrastructure/dispatch/CLI split (separate follow-up).
- Any `server_ops.py` extraction. This task moves no ops code.
- Deleting the 40 explicit names or reshaping what `server` publishes.
- Adding `server.py` to the mypy clean gate. A module `__getattr__` makes
  unknown attributes `Any` for mypy, so gating this module is a separate
  decision that must be made with that fact in hand.

## Acceptance Criteria

- [x] All 227 historic names resolve through `server`, proven by a test that
      derives the list from `server_ops` at runtime rather than hard-coding it.
- [x] An unknown attribute raises `AttributeError`.
- [x] `hasattr(server, "__all__")` is still `False`, with its own test.
- [x] `dir(server)` includes the delegated names.
- [x] `.venv/bin/pytest` is green, with no edits to existing test assertions.
- [x] `server.py` line count drops and its `tools/check_module_size.py`
      ratchet ceiling is lowered to the new exact size in the same diff.
- [x] CLAUDE.md and `.trellis/spec/amc/backend/architecture.md` record that new
      ops names no longer need a `server.py` alias line.
- [x] The epic's `implement.md` records this seam as settled.
