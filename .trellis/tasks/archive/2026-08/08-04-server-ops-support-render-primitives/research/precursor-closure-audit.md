# Research: Precursor closure audit — server_ops render primitives extraction

- **Query**: Closure audit for verbatim extraction of `CommandResult` + 5 helpers (`_table`, `_format_dt`, `_is_dry_run`, `_unsupported`, `_exposed_active_scenarios`) out of `server_ops.py`, precursor to the parked `server_helm_impl.py` step of epic `07-06-server-ops-decomposition`.
- **Scope**: internal (read-only)
- **Date**: 2026-08-04
- **Branch verified**: `main`
- **Repo**: `platypeeps/anomaly-metric-creator` (repo root)

All line numbers below are against `src/anomaly_metric_creator/server_ops.py` as it currently stands on `main` (5590 lines).

---

## 1. Exact move-set with line ranges

| Symbol | Kind | Def line | Full span | Notes |
|---|---|---|---|---|
| `CommandResult` | `@dataclass(frozen=True)` class | 157 (decorator 156) | **156–162** | 5 typed fields; decorator on 156, last field `matched_rule_id: str` on 162; blank 163–164 before `KubernetesApiResponse` (165). |
| `_is_dry_run` | function | 491 | **491–497** | `def _is_dry_run(parsed: ParsedCommand) -> bool:` |
| `_unsupported` | function | 686 | **686–693** | `def _unsupported(parsed: ParsedCommand, label: str) -> CommandResult:` |
| `_exposed_active_scenarios` | function | 3379 | **3379–3393** | 13-line docstring (3380–3392), body `return () if state.eval_mode else state.active_scenarios` (3393). |
| `_table` | function | 3571 | **3571–3579** | `def _table(headers: list[str], rows: list[list[str]]) -> str:` |
| `_format_dt` | function | 3589 | **3589–3590** | `def _format_dt(value: _dt.datetime) -> str:` — one-line body. |

All six are exported in `server_ops.py`'s `__all__`:
- `'CommandResult'` (5374), `'_unsupported'` (5394), `'_exposed_active_scenarios'` (5458), `'_table'` (5472), `'_format_dt'` (5480).
- **`_is_dry_run` is NOT in `__all__`** (confirmed — grep returns nothing). It is an intra-module helper only; no facade re-exports it. Its re-import into `server_ops` is still needed for the intra-module callers (§5) but no `__all__` line needs adding.

---

## 2. Per-symbol dependency closure

Classification legend: **(a)** stdlib/typing, **(b)** already in an importable lower leaf, **(c)** STAYS in server_ops (blocker).

### `CommandResult` (156–162)
- References: `dataclass` (a, stdlib `dataclasses`), field types `int`/`str` (a). **No references to any server_ops symbol.**
- Verdict: **freely movable** to any leaf. It is a pure typed dataclass.

### `_format_dt` (3589–3590)
- References: `_dt.datetime` (a, stdlib `datetime`), `.strftime` (a).
- **Special case — byte-identical duplicate already exists.** `server_mutations.py:16–17` defines:
  ```python
  def _format_dt(value: _dt.datetime) -> str:
      return value.strftime("%Y-%m-%d %H:%M:%S")
  ```
  This is character-for-character identical to `server_ops.py:3589–3590`. `server_ops` currently keeps its **own local copy** (it imports `DEFAULT_NAMESPACE, HelmReleaseMutation, SimulationMutations, WorkloadMutation, _mutation_resource_key, _resource_prefix` from `server_mutations` at lines 26–33 but NOT `_format_dt`).
- Verdict: **does not need to move at all.** Simplest closure: delete the local copy and re-import from `server_mutations` (already the bottom of the DAG, already imported here). This removes a real duplicate. Independent of the render-leaf decision. (`server_mutations` internally uses `_format_dt` at lines 161/224/228/326/332/338, so the definition must stay there.)

### `_is_dry_run` (491–497)
- References: `ParsedCommand` (type annotation on `parsed` param — **(b)** `server_ops_parse.ParsedCommand`, class defined at `server_ops_parse.py:24`), `parsed.flags.get`, `str`, `.strip`, `.lower` (a).
- **No CommandResult, no SimulationState, no other server_ops symbol.**
- Verdict: needs only `ParsedCommand` from `server_ops_parse` (a sibling leaf). Movable to any leaf that can import `server_ops_parse`.

### `_unsupported` (686–693)
- References: `ParsedCommand` (b, `server_ops_parse`), **`CommandResult` (constructs it)**, `str` (a). `parsed` param is unused in the body (only `label`).
- **Blocker resolved:** yes, `_unsupported`'s `CommandResult(...)` construction **forces `CommandResult` to co-move** into the same leaf (or a lower leaf the chosen leaf imports). Since `CommandResult` currently lives in `server_ops` and the epic rule forbids a leaf importing `server_ops`, `_unsupported` cannot move unless `CommandResult` moves with it (or ahead of it).
- Verdict: co-moves with `CommandResult`; also needs `ParsedCommand` from `server_ops_parse`.

### `_exposed_active_scenarios` (3379–3393)
- References: `SimulationState` (type annotation on `state` param), body reads `state.eval_mode` and `state.active_scenarios`, returns `tuple` (a).
- `SimulationState` is a dataclass **defined in `server_ops.py:203`** — it STAYS in server_ops.
- **Blocker resolved:** the reference is **annotation-only**. With `from __future__ import annotations` (already present in every leaf), the annotation is stringized and never evaluated at runtime. The body accesses only `state.eval_mode`/`state.active_scenarios` attributes on a runtime object passed in by the caller — no runtime reference to the `SimulationState` *name*. This is the exact pattern `server_ops_support.py` already uses for `_k8s_list_resource_version(state: SimulationState)` (lines 24–25, 78–80):
  ```python
  if TYPE_CHECKING:  # never executed, one-way rule holds
      from .server_ops import SimulationState
  ```
  So `_exposed_active_scenarios` needs **only a `TYPE_CHECKING` import of `SimulationState`** — not a runtime import, not a co-move of `SimulationState`. No blocker.

### `_table` (3571–3579)
- References: `len`, `max`, `str`, `.ljust`, `.join`, `enumerate`, list comprehensions (all a).
- **No server_ops symbol.** Fully self-contained.
- Verdict: freely movable.

### Closure summary

| Symbol | (a) stdlib | (b) lower-leaf import | (c) blocker → resolution |
|---|---|---|---|
| `CommandResult` | `dataclasses`, `int`/`str` | — | none |
| `_format_dt` | `datetime` | (identical copy in `server_mutations`) | none — re-import from `server_mutations`, drop duplicate |
| `_is_dry_run` | `str` ops | `ParsedCommand` ← `server_ops_parse` | none |
| `_unsupported` | `str` | `ParsedCommand` ← `server_ops_parse` | **`CommandResult` co-move required** (currently in server_ops) |
| `_exposed_active_scenarios` | `tuple` | — | `SimulationState` → **TYPE_CHECKING annotation-only** (already-proven pattern) |
| `_table` | `len/max/str/ljust/join` | — | none |

The only hard coupling is `_unsupported → CommandResult` (forces co-move). Everything else is stdlib, a `server_ops_parse` import, or a TYPE_CHECKING annotation.

---

## 3. Leaf-choice recommendation

### Facts checked
- `server_ops_support.py` currently imports **only** `from .server_mutations import DEFAULT_NAMESPACE` (line 22) plus stdlib (`contextlib`, `datetime`, `typing`). Its docstring states its charter verbatim: *"Stdlib + `server_mutations`-only leaf … Owns the release/chart identity constants and the snapshot-row / timestamp / string-coercion / list-resource-version accessors."*
- `server_ops_support.py` does **NOT** currently import `server_ops_parse`.
- `server_ops_parse.py` imports only `shlex` (stdlib) + `from .server_mutations import DEFAULT_NAMESPACE`. It does **not** import `server_ops_support`.
- Adding `from .server_ops_parse import ParsedCommand` to `server_ops_support` would be **acyclic** (support → parse; parse never imports support). So a cycle is not the blocker either way.

### Recommendation: **NEW leaf `server_command_render.py`** (firm)

Rationale:
1. **CommandResult co-move poisons the support leaf's charter.** `_unsupported` forces `CommandResult` into the chosen leaf. `server_ops_support.py` is imported by `server_k8s_objects.py` and `server_k8s_tables.py` (neither needs `CommandResult`). Parking a command-result dataclass + `_unsupported`/`_is_dry_run`/`_table`/`_exposed_active_scenarios` inside a module whose stated single purpose is "snapshot-row / timestamp / string-coercion accessors" breaks the honest-single-purpose-description test that CLAUDE.md frames as the extraction gate. The new-leaf name self-documents "command render primitives + `CommandResult`."
2. **The support leaf's "stdlib + `server_mutations`-only" charter is stated verbatim in its docstring.** `_is_dry_run` and `_unsupported` need `ParsedCommand` from `server_ops_parse`; adding that import to `server_ops_support` contradicts its own docstring and would need a docstring rewrite plus re-justification. A new leaf declares `from .server_ops_parse import ParsedCommand` cleanly.
3. **Clean single import source for the future `server_helm_impl.py`.** All six symbols the parked helm step needs (`CommandResult`, `_table`, `_format_dt`, `_is_dry_run`, `_unsupported`, `_exposed_active_scenarios`) land in one purpose-named leaf. `server_helm_impl` imports from `server_command_render` (+ `server_mutations` for `_format_dt` if that route is chosen) with no server_ops dependency.

### New-leaf import shape (proposed)
```python
# server_command_render.py
from __future__ import annotations
import datetime as _dt              # only if _format_dt is defined here rather than re-imported
from dataclasses import dataclass
from typing import TYPE_CHECKING
from .server_ops_parse import ParsedCommand
from .server_mutations import _format_dt as _format_dt   # byte-identical; avoids a 3rd copy
if TYPE_CHECKING:
    from .server_ops import SimulationState
```
- `_format_dt`: recommend **re-import from `server_mutations`** rather than a third verbatim copy (it is byte-identical). If the design prefers all render primitives physically co-located, defining it in the new leaf is acceptable but creates a 3rd identical body; the re-import is cleaner.
- Resulting DAG edge additions: `server_mutations → server_command_render`, `server_ops_parse → server_command_render`. Both acyclic (parse/mutations never import the render leaf). `server_command_render` sits as a sibling/peer alongside `server_ops_support` and `server_ops_parse`, below `server_ops`.

### Runner-up (documented, not recommended)
Extend `server_ops_support.py`. Works mechanically (acyclic), fewer new files, and the task slug hints at it (`…-support-render-primitives`). Rejected because it dilutes the support leaf's stated purpose and forces its "stdlib + server_mutations only" docstring to be rewritten to admit `server_ops_parse` + a command dataclass that its k8s-builder consumers never use.

---

## 4. CommandResult blast radius

### Definition + construction sites
- **Definition**: `server_ops.py:157` (span 156–162) — the only class def.
- **Constructions**: ~90 `CommandResult(...)` call sites, **all inside `server_ops.py`** (render/patch/apply/create/manifest/helm paths). Representative: 502, 507, 523, 535–627 (kubectl), 635–692 (helm + `_unsupported`), 1019, 1234–1487 (describe), 1504–1545 (logs), 1704–1754 (explain), 2175–2267 (patch), 2397–2455 (diff/apply/create), 2492–2640 (manifest), 3182 (`_not_found`). No construction happens outside `server_ops.py`.

### Import / attribute-access sites (whole repo, src/)
| Site | Form | Resolves via |
|---|---|---|
| `server_ops.py:157` | class def | — |
| `server.py:304` | `CommandResult = _server_ops.CommandResult` | attribute on `server_ops` module |
| `server_commands.py:7` | `from .server_ops import ( … CommandResult as CommandResult … )` | name bound in `server_ops` namespace |
| `server_commands.py:27` | `'CommandResult'` in `__all__` | — |
| `server_ops.py:5374` | `'CommandResult'` in `__all__` | — |

### Tests
- `tests/test_server.py:2648` — `assert server.CommandResult is commands.CommandResult` (**identity assertion** across the two facades).

### Verdict
If `CommandResult` moves to `server_command_render.py` and `server_ops.py` re-imports it at its conceptual position (line ~156), then:
- `_server_ops.CommandResult` (server.py:304) resolves — the name is bound in the `server_ops` module namespace by the re-import.
- `from .server_ops import CommandResult` (server_commands.py:7) resolves for the same reason.
- The identity assertion `server.CommandResult is commands.CommandResult` (test_server.py:2648) still holds: both trace back through the single re-import to the one leaf-defined class object.
- All ~90 intra-`server_ops` `CommandResult(...)` constructions resolve in the `server_ops` namespace via the re-import (the CLAUDE.md "intra-module call resolves in server_ops namespace" rule).

**Required re-import stub in `server_ops.py`** (at CommandResult's original position, replacing the class def):
```python
from .server_command_render import (
    CommandResult as CommandResult,
    _table as _table,
    _is_dry_run as _is_dry_run,
    _unsupported as _unsupported,
    _exposed_active_scenarios as _exposed_active_scenarios,
)
# _format_dt: from .server_mutations import _format_dt as _format_dt   (drop the local copy)
```
(`_format_dt` re-import comes from `server_mutations` under the recommended plan; if it is instead defined in the render leaf, add it to the block above.)

---

## 5. Intra-module callers of the 5 helpers — re-import resolution

All caller sites are inside `server_ops.py` and resolve in the `server_ops` namespace through the module-level re-import (per the CLAUDE.md "intra-module call resolves in server_ops's namespace" note). Confirmed call sites:

- **`_format_dt`** (13 calls): 121, 440, 1601, 2973, 3033, 3035, 3063, 3434, 3508, 4317 (+ `to_dict`/helm-history/notes). All resolve via re-import.
- **`_exposed_active_scenarios`** (5 calls): 478, 727, 2920, 3008, 5147.
- **`_unsupported`** (16 calls): 563, 567, 575, 581, 587, 593, 599, 605, 611, 630, 652, 683, 1018, 1494 (+ its own def). All resolve via re-import.
- **`_table`** (many): 1041–1163 (kubectl get tables), 1640/1644 (top), 1691 (api-resources), 2002 (rollout history), 2940/2945 (helm list), 2981 (helm history), 3036 (helm test). All resolve via re-import.
- **`_is_dry_run`** (4 calls): 2425, 2463, 3043, 3073.

### Monkeypatch / setattr scan
Grepped `tests/test_server.py`, `tests/test_server_ops_fuzz.py`, `tests/test_server_mcp.py`, `tests/test_server_eval_mode.py`, `tests/test_server_watch.py`, `tests/test_server_hardening.py`, `tests/test_server_reset.py` for `monkeypatch.setattr` / `setattr` targeting any of the 6 symbols:
- **No monkeypatch/setattr site targets any of `CommandResult`, `_table`, `_format_dt`, `_is_dry_run`, `_unsupported`, `_exposed_active_scenarios`.**
- The only direct test references are **read-only facade access**: `server._format_dt(...)` (test_server.py:2529–2538), `server.CommandResult` / `commands.CommandResult` (test_server.py:2648). No test patches these names, so there is no namespace-of-patch hazard (unlike the `_wide_component_rows_are_monotonic` case CLAUDE.md warns about). The re-import stub fully preserves them.

---

## 6. Splice hazards (grep of deleted ranges for `^from \.` blocks)

The epic's splice-hazard rule: a line-range cut can sweep up a prior extraction's re-import stub. Checked each move-set span for interleaved `from .` re-imports:

- `CommandResult` (156–162): clean body, no `from .` inside. **HAZARD ADJACENT:** the `from .server_ops_parse import (…)` re-import block sits at **133–153**, immediately above (only blank lines 154–155 separate it from the `@dataclass` at 156). A careless cut that starts the CommandResult removal earlier than line 156 (e.g. "from the parse import block down to KubernetesApiResponse") would delete the `server_ops_parse` re-import and break `ParsedCommand`/`parse_command`/etc. resolution. **Anchor the cut at line 156 exactly.**
- `_is_dry_run` (491–497): no `from .` inside. Clean.
- `_unsupported` (686–693): no `from .` inside. Clean.
- `_exposed_active_scenarios` (3379–3393): no `from .` inside (docstring only). Clean.
- `_table` (3571–3579): no `from .` inside. Clean.
- `_format_dt` (3589–3590): no `from .` inside. Clean.

Post-extraction, re-run the epic's standard check: grep the edited ranges and confirm every prior leaf re-import (`server_mutations`, `server_ops_support`, `server_ops_profiles`, `server_ops_parse`, `server_traces`, `server_k8s_objects`, `server_k8s_tables`, and the new `server_command_render`) still resolves.

---

## 7. mypy gate

- **Gate membership**: `tools/check_mypy_gate.py` `CLEAN_MODULES` (lines 17–46) includes `server_ops_support.py` (line 44) and `server_ops_parse.py` (line 42), `server_mutations.py` (41), `server_k8s_objects.py` (37), `server_k8s_tables.py` (38). **`server_ops.py` is deliberately NOT in the gate** (it is the ~137-error messy baseline).
- **If the new leaf is created**: add `src/anomaly_metric_creator/server_command_render.py` to `CLEAN_MODULES` (per the epic rule "grow the list as decomposition extracts clean modules; never drop one to silence a regression"). If instead `server_ops_support.py` is extended, it is already in the gate — the moved symbols must type-check clean there.
- **var-annotate / type-gap risk of the moved symbols**: none identified.
  - `_table`: `widths = [len(h) for h in headers]` → inferred `list[int]`; `lines = ["  ".join(...)]` → inferred `list[str]`. No annotation needed.
  - `_format_dt`, `_is_dry_run`, `_unsupported`, `_exposed_active_scenarios`, `CommandResult`: all fully annotated signatures / typed dataclass fields; no `next(..., {})`-style inference gap (the one `var-annotate` gap CLAUDE.md records for `server_k8s_tables` does not appear in any of these six).
  - The gate runs `mypy --follow-imports=silent` over the listed files, so the render leaf importing `server_ops.SimulationState` under `TYPE_CHECKING` and `server_ops_parse.ParsedCommand` at runtime is checked for inference but reports only errors originating in the listed files — importing still-dirty `server_ops` for the TYPE_CHECKING name does not leak its baseline errors into the gate.

---

## 8. Test + oracle gates to run

### pytest files (server-mode surface — must stay green before/after)
- `tests/test_server.py` (core render + facade identity: `server._format_dt`, `server.CommandResult is commands.CommandResult` @ 2648)
- `tests/test_server_ops_fuzz.py` (seeded malformed-input / render degradation corpus)
- `tests/test_server_mcp.py` (MCP tool wrappers dispatch through render helpers)
- `tests/test_server_eval_mode.py` (**directly exercises `_exposed_active_scenarios` behavior**: rubric-leak sweep + eval/non-eval control)
- `tests/test_server_watch.py`
- `tests/test_server_hardening.py`
- `tests/test_server_reset.py`
- `tests/test_trace_bundle.py` (offline trace-bundle over `server_traces`)

Run all `tests/test_server*.py` + `tests/test_trace_bundle.py` under xdist; they are not in the `heavy` fixture set, so `pytest -n 2 --dist loadfile tests/test_server*.py tests/test_trace_bundle.py` is the fast partition.

### Render-oracle byte-identity command list (before == after)
The extraction is verbatim, so `render_command(state, parse_command(cmd))` stdout/stderr/exit_code/support_status/matched_rule_id must be byte-identical before and after for every command that flows through the 5 helpers. Minimum oracle set (each touches a moved helper):

| Command | Helper(s) exercised |
|---|---|
| `kubectl get pods -n saas-prod` | `_table` |
| `kubectl get deployments -n saas-prod` | `_table` |
| `kubectl get events -n saas-prod` | `_table` |
| `kubectl describe pods <pod>` | (describe → `CommandResult`) |
| `kubectl logs <pod>` | `_format_dt` (log timestamp @ 1601), `CommandResult` |
| `helm list` | `_table`, `_format_dt` (2940–2945) |
| `helm status simulated-saas` | `_format_dt` (LAST DEPLOYED @ 3063), `CommandResult` |
| `helm history simulated-saas` | `_table`, `_format_dt` (2973/2981) |
| `helm get values simulated-saas` | `_exposed_active_scenarios` (3008) |
| `kubectl exec <pod> -- env` | `_exposed_active_scenarios` (2920) |
| `kubectl apply --dry-run=client -f <manifest>` | `_is_dry_run` (2425/2463) |
| `helm upgrade --dry-run …` | `_is_dry_run` (3043/3073) |
| any unsupported verb, e.g. `kubectl cordon <node>` | `_unsupported` |
| `--mcp-eval-mode` variants of the `helm get values` / `kubectl exec env` / configmap `SCENARIOS` renders | `_exposed_active_scenarios` empty-in-eval path |

Capture each command's `CommandResult` tuple on `main` before the change and re-assert byte-identical after (this is the epic's oracle-diff gate; `tests/test_server.py` already pins many of these, and `tests/test_server_eval_mode.py` pins the eval-mode scenario-leak variants).

### Mechanical gates
- `.venv/bin/python3 tools/check_mypy_gate.py` (with the new module added to `CLEAN_MODULES`).
- `.venv/bin/ruff check` over changed files (F401 on the re-import stub — use `as`-aliased re-exports so unused-import does not fire).

---

## Caveats / Not Found

- No dedicated "render oracle" harness file exists in `tests/`; oracle coverage is embedded in `tests/test_server.py` assertions. The design may want a small explicit before/after snapshot check for the command list in §8 if byte-identity is to be pinned mechanically rather than by the existing scattered assertions.
- `python3 .trellis/scripts/task.py current` reports a *different, stale* task (`07-27-trellis-placeholder-split-batch`, session-fallback). Findings were written to the explicitly-named task dir `08-04-server-ops-support-render-primitives/research/` per the task instructions, not to the resolver's output.
- The task slug (`…-support-render-primitives`) leans toward the "extend `server_ops_support`" option; this audit recommends the **new-leaf** option on single-purpose grounds. Flagging the naming tension for the design author to reconcile explicitly.
