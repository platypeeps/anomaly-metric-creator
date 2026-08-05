# Implement — server_command_render.py extraction

Ordered execution. Each numbered step ends with a check; do not advance on a red
check. All line numbers are against `server_ops.py` on `main` (5,590 lines) per
`research/precursor-closure-audit.md`; re-grep before cutting in case an
intervening merge shifted them.

## Step 0 — render oracle baseline (before any edit)

Build a scratch harness that drives `run_command` over the audit §8 command list
and dumps each `CommandResult` tuple.

```bash
mkdir -p <scratch>/render_oracle
# write <scratch>/render_oracle/oracle.py: build_state(...) then run_command per cmd,
#   json.dumps sorted (exit_code, stdout, stderr, support_status, matched_rule_id)
.venv/bin/python <scratch>/render_oracle/oracle.py > <scratch>/render_oracle/before.json
```

Command list (each row exercises a moved symbol):
`kubectl get pods -n saas-prod`, `kubectl get deployments -n saas-prod`,
`kubectl get events -n saas-prod` (`_table`); `kubectl describe pod …`,
`kubectl logs …` (`_format_dt`); `helm list`, `helm status <rel>`,
`helm history <rel>` (`_table`/`_format_dt`); `helm get values <rel>`,
`kubectl exec … -- env` (`_exposed_active_scenarios`);
`kubectl apply -f x --dry-run=client`, `helm upgrade … --dry-run`
(`_is_dry_run`); a bogus verb `kubectl frobnicate` (`_unsupported`); plus each
scenario-bearing variant with `--mcp-eval-mode` on (empty-scenario path).

**Check:** `before.json` non-empty, every command produced a tuple, no
exception. This file is the oracle; keep it out of the commit.

## Step 1 — create branch

```bash
git switch -c sdelmas/server-command-render-extract
```

**Check:** `git branch --show-current` = the new name (branch-name lint: no
`ver-\d+`, clean).

## Step 2 — pre-move monkeypatch / closure grep (confirm audit)

```bash
grep -rnE 'server_ops\.(CommandResult|_table|_is_dry_run|_unsupported|_exposed_active_scenarios|_format_dt)' src tests
grep -rnE '(monkeypatch|setattr)\b.*\b(CommandResult|_table|_is_dry_run|_unsupported|_exposed_active_scenarios)\b' tests
```

**Check:** no monkeypatch/setattr on the six symbols (audit §5). Access sites =
only server.py:304, server_commands.py:7, test identity assert. If a new site
appeared since the audit, note it — the re-import stub still covers it, but
record it in the PR body.

## Step 3 — create the leaf `server_command_render.py`

New file `src/anomaly_metric_creator/server_command_render.py` with the docstring
+ import header from design.md, then the **verbatim** bodies of `CommandResult`
(156–162), `_table` (3571–3579), `_is_dry_run` (491–497), `_unsupported`
(686–693), `_exposed_active_scenarios` (3379–3393) — copied byte-for-byte, no
edits to logic. `_exposed_active_scenarios` keeps its `SimulationState`
annotation (resolved by the TYPE_CHECKING import; `from __future__ import
annotations` stringizes it). Do **not** add `_format_dt` here (re-exported from
server_mutations).

`__all__` on the leaf lists the five public names
(`CommandResult`, `_table`, `_unsupported`, `_exposed_active_scenarios`, plus
`_is_dry_run` for symmetry — leaf-level `__all__` is not the server_ops one).

**Check:**
`grep -nE 'from \.server_ops|import server_ops' src/anomaly_metric_creator/server_command_render.py`
= **0 hits** (one-way rule). `.venv/bin/python -c "import
anomaly_metric_creator.server_command_render"` imports clean.

## Step 4 — delete originals + install re-import stubs in server_ops.py

Anchor edits so the `server_ops_parse` re-import block (133–153) is untouched.

1. At line **156** (the `@dataclass` / `CommandResult` def), replace the
   CommandResult class body with the render-leaf re-import block from design.md
   (`from .server_command_render import (CommandResult as CommandResult, _table
   as _table, _is_dry_run as _is_dry_run, _unsupported as _unsupported,
   _exposed_active_scenarios as _exposed_active_scenarios,)`) **plus**
   `from .server_mutations import _format_dt as _format_dt`.
2. Delete the now-duplicated defs at their original spots: `_is_dry_run`
   491–497, `_unsupported` 686–693, `_exposed_active_scenarios` 3379–3393,
   `_table` 3571–3579, `_format_dt` 3589–3590. (Removing the earlier stub-covered
   defs; each name now resolves via the line-156 block.)
3. Leave `server_ops.__all__` unchanged (audit §5: the five public names stay,
   `_is_dry_run` was never listed).

**Check (splice hazard):**
```bash
grep -nE '^from \.' src/anomaly_metric_creator/server_ops.py | sed -n '1,40p'
```
Confirm the `from .server_ops_parse import (…)` block still resolves and was not
swept into the CommandResult cut. Then:
```bash
.venv/bin/python -c "import anomaly_metric_creator.server_ops as s; \
  import anomaly_metric_creator.server_command_render as r; \
  assert s.CommandResult is r.CommandResult; \
  assert s._table is r._table and s._unsupported is r._unsupported; \
  assert s._exposed_active_scenarios is r._exposed_active_scenarios; \
  import anomaly_metric_creator.server_mutations as m; assert s._format_dt is m._format_dt; \
  print('identity-ok')"
```

## Step 5 — compat imports resolve

```bash
.venv/bin/python -c "import anomaly_metric_creator.server, anomaly_metric_creator.server_mcp, \
  anomaly_metric_creator.server_commands, anomaly_metric_creator.server_kubernetes, \
  anomaly_metric_creator.server_helm; \
  from anomaly_metric_creator import server, server_commands; \
  assert server.CommandResult is server_commands.CommandResult; print('compat-ok')"
```

**Check:** prints `compat-ok`, no ImportError.

## Step 6 — render oracle after

```bash
.venv/bin/python <scratch>/render_oracle/oracle.py > <scratch>/render_oracle/after.json
diff -u <scratch>/render_oracle/before.json <scratch>/render_oracle/after.json
```

**Check:** empty diff. Any byte diff = behavior regression = fix before advancing.

## Step 7 — mypy gate

Add `src/anomaly_metric_creator/server_command_render.py` to
`tools/check_mypy_gate.py` `CLEAN_MODULES` (keep alphabetical / grouped with the
server leaves).

```bash
.venv/bin/python3 tools/check_mypy_gate.py
```

**Check:** exit 0, no error attributed to the new leaf. If a verbatim-inherited
`var-annotate` surfaces (audit says none expected), close it with an explicit
annotation before the module joins the gate.

## Step 8 — targeted + full suite

```bash
.venv/bin/pytest tests/test_server.py tests/test_server_ops_fuzz.py \
  tests/test_server_mcp.py tests/test_server_eval_mode.py \
  tests/test_server_watch.py tests/test_server_hardening.py \
  tests/test_server_reset.py tests/test_trace_bundle.py -n 0
.venv/bin/ruff check src/anomaly_metric_creator/server_command_render.py \
  src/anomaly_metric_creator/server_ops.py
.venv/bin/pytest
```

**Check:** targeted green first, then ruff clean, then full suite exit 0 with 0
new failures vs the pre-change baseline.

## Step 9 — docs in the same diff

- CLAUDE.md server module map: add `server_command_render.py` to the DAG
  sentence and the leaf inventory (peer of `server_ops_support` /
  `server_ops_parse`, below `server_ops`); note `_format_dt` dedup onto
  `server_mutations`.
- `.trellis/spec/amc/backend/architecture.md` (or the server-map spec): mirror
  the same DAG line.
- Refresh the KB after doc edits:
  `.venv/bin/python scripts/sd-ai-command-pack-update-spec-kb.py`.

**Check:** `grep -n server_command_render CLAUDE.md` returns the new lines;
`.venv/bin/python scripts/sd-ai-command-pack-update-spec-kb.py --check` exit 0.

## Step 10 — record line count + PR body

```bash
wc -l src/anomaly_metric_creator/server_ops.py src/anomaly_metric_creator/server_command_render.py
```

Record the new `server_ops.py` end line count in the PR body and in the epic
`07-06-server-ops-decomposition/implement.md` step status (this precursor +
"step 3 helm now truly unblocked"). Work the 15-heading pre-PR checklist before
the PR leaves draft.

## Rollback points

- After step 4 the change is self-contained; if any check reds, `git restore`
  the two files and the gate line.
- The oracle diff (step 6) is the behavior gate; the identity asserts (steps
  4–5) are the wiring gate. A red on either is a hard stop, not a warn.
