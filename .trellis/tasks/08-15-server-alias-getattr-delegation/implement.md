# Implementation Plan — `server.py` alias delegation

## Pre-flight check (name it before the work, per the verification rule)

The check that catches this being wrong is **not** "the suite is green" — a
delegated name that `server.py` reads as a bare global fails at request time,
and only on the path that reads it.

Decisive checks, with failure defined up front:

1. `.venv/bin/pytest tests/test_server.py tests/test_server_ops_fuzz.py
   tests/test_server_mcp.py tests/test_server_eval_mode.py
   tests/test_serve_main_wiring.py -n 0` — expect 0 failures, 0 errors. Any
   `NameError` is the internal-global failure mode.
2. A pre/post attribute-surface diff over the **227 historic names** captured
   from `git show <base>:src/anomaly_metric_creator/server.py`: every name must
   resolve post-change and be the same object `server_ops` exposes. Expect an
   empty diff; one missing name is a failure.
3. `python tools/check_module_size.py` — expect exit 0. Note what this does
   **not** prove: the lint flags an enrolled module only when it exceeds its
   ceiling or falls to/under the 800-line cap, so a `server.py` that shrank to
   2,064 under a stale 2,208 ceiling passes silently. The ceiling being
   lowered is verified by `git diff tools/check_module_size.py` showing the new
   exact `wc -l` of `server.py`, not by this command's exit code.
4. `python -c "import anomaly_metric_creator.server as s; print(hasattr(s, '__all__'))"`
   — expect `False`. `server_ops.__all__` exists and `server.py` defines none;
   `True` here means the dunder guard is missing and star-import semantics
   changed.
5. `.venv/bin/pytest` full suite + `.venv/bin/pre-commit run --all-files`.

## Execution Order

1. **Capture the baseline.** From clean `main`, dump the 227 historic names
   and the `repr`/identity of each `getattr(server, n)` to a scratch JSON.
   This is check 2's left-hand side and must be captured *before* any edit.
2. **Compute the explicit-40 set mechanically**, not by hand: AST-scan
   `server.py` for bare-global loads of aliased names (30), repo-grep for
   `server.<name>` reads outside `server.py` (24), union (40). Write the
   result to scratch so the diff can be reproduced in review.
3. **Edit `server.py`**:
   - replace the 227 assignment lines with one
     `from .server_ops import (name as name, ...)` block holding the 40, kept
     in the block's current order so the diff reads as a deletion plus a
     regroup rather than a reshuffle;
   - add the comment recording *why* those 40 are explicit (PEP 562 does not
     cover internal global resolution);
   - add `__getattr__` and `__dir__` at the end of the compatibility block,
     not at module end, so the seam stays at its historic conceptual location
     — the same rule the legacy epic uses for re-import stubs.
4. **Splice-hazard grep** — the epic's standing rule after cutting a line
   range: `grep -n '^from \.' src/anomaly_metric_creator/server.py` and
   confirm nothing inside the removed range was a re-import stub. Measured
   2026-08-15: lines 309-535 are **homogeneous** — 227 assignment lines and
   nothing else, no blanks, no interleaved imports. (The interleaved-stub
   pattern the epic warns about is real, but it lives in `server_ops.py`, not
   here.) Re-run the grep anyway rather than trusting this note: the range
   moves every time an extraction lands.
5. **Add `tests/test_server_alias_surface.py`** — the five tests in design.md
   § 6.
6. **Negative verification** — prove the new tests are not vacuous, in two
   independent directions rather than one:
   - temporarily delegate one of the 40 (e.g. `_is_kubernetes_api_path`) and
     confirm `test_explicit_binds_cover_every_internal_use` fails;
   - temporarily drop the dunder guard and confirm
     `test_dunder_names_are_not_delegated` fails.
   Revert both. Record both results in the PR body.
7. **Lower the `server.py` ratchet ceiling** in
   `tools/check_module_size.py` to the new exact line count, with a reason
   naming this task.
8. **Docs and specs in the same diff:**
   - `CLAUDE.md` — the module-ownership section: a new ops name no longer
     needs a `server.py` alias line;
   - `.trellis/spec/amc/backend/architecture.md` — the module-boundaries
     section: record the delegation and the explicit-40 rule;
   - the epic's `implement.md` — mark the `server.py` seam settled and correct
     the step-5 follow-up (see below);
   - `CHANGELOG.md` if the published surface note warrants it.
9. **Ship** — `sd-ship until=merge`.

## Correction to carry into the epic's `implement.md`

The step-5 follow-up reads:

> move `_openapi_paths` + snapshot-kind constants to let the OpenAPI document
> builders move too

A closure audit on 2026-08-15 shows the premise does not hold.
`_openapi_paths` (68 lines) is blocked only by `_snapshot_kind_namespaced` and
is movable, but the document builders `_k8s_openapi_v2_document` /
`_k8s_openapi_v3_document` are blocked by `_openapi_schema_definitions`, which
calls `resource_snapshot(state)` and `_explain_schema_for_kind` — the
state-bound spine step 5 deliberately left behind. Moving `_openapi_paths`
therefore frees ~75 lines and unblocks nothing further; the document builders
need the same step-6b provider seam as the render cluster. Record this so the
next planner does not re-derive it.

## Validation Plan

```bash
.venv/bin/pytest tests/test_server_alias_surface.py -n 0
.venv/bin/pytest tests/test_server.py tests/test_server_ops_fuzz.py \
  tests/test_server_mcp.py tests/test_server_eval_mode.py \
  tests/test_serve_main_wiring.py -n 0
python tools/check_module_size.py
.venv/bin/pytest
.venv/bin/pre-commit run --all-files
scripts/sd-ai-command-pack-full-check.sh
```

## Rollback

Single commit; revert restores the assignment block. No persisted state.

## Review Gates

- The explicit-40 list in the diff matches the mechanically computed set —
  reviewers can re-run step 2 rather than eyeball 227 lines.
- Check 2's pre/post surface diff is quoted in the PR body, empty.
- Negative verification (step 6) result stated explicitly.
