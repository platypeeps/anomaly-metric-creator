# MCP tool scans + trace-store hot paths — Implementation Plan

## Execution Order

1. Branch from `main`. Write the timing harness first (scratch script:
   narrow-window histogram + timeline + summary() at 0/5k traces) and
   capture the BEFORE numbers.
2. A-039: boundary-string precompute + parse-gate + column-index hoist +
   layout-gated break in the three server_mcp scan sites. Add the N=3
   no-break correctness test (tiny `--instances-per-component 3` run).
3. A-042: snapshot hoist (small, independent — do it while server_ops is
   open).
4. A-040: GROUP BY aggregation + COUNT for summary(); verify EXPLAIN,
   add fingerprint index via schema-version migration only if scanned.
5. A-041: long-lived connection under the store lock + persistent JSONL
   handle outside the main lock + retention every 64 inserts.
6. Capture AFTER numbers; build the PR timing table.
7. Flip A-039/A-040/A-041/A-042 → `fixed` (same PR).
8. Draft PR → checklist (performance heading) → ready → merge.

## Validation Plan

```bash
.venv/bin/pytest tests/test_server_mcp.py tests/test_server.py tests/test_trace_bundle.py -n 0
.venv/bin/pytest tests/test_server_eval_mode.py -n 0   # scans feed eval-walled tools too
.venv/bin/pytest && .venv/bin/pre-commit run --all-files
.venv/bin/python <scratch>/timing_harness.py            # before/after table
```

## Documentation And Spec Updates

- Docstrings at each changed site (why the break is layout-gated; the
  retention-every-N contract). CLAUDE.md only if the MCP section states
  per-call scan behavior (grep first).

## Review Notes

- Lead with the timing table and the no-break-on-dim-layout test — those
  are the two things a reviewer must be able to verify without running
  anything.
- SQLite change is concurrency-sensitive: document the lock ownership
  rule in the store class docstring.

## Follow-Ups

- If concurrent-agent load ever matters, a shared parsed-column cache
  keyed on (file, mtime) is the next step — out of scope now (needs an
  invalidation story).
