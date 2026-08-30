# server_ops_parse.py extraction — Implementation Plan

## Ordered checklist

1. **Baseline oracle** — captured (33-command corpus, pure parse-cluster
   funcs) at `<scratch>/oracle_baseline.json`.
2. **AST extraction script** — a scratch Python script parses
   `server_ops.py`, extracts the 26 move-set symbols by exact AST span
   (decorators included for `ParsedCommand`), writes
   `src/anomaly_metric_creator/server_ops_parse.py` (header + imports +
   verbatim blocks, source order), and writes the new `server_ops.py`
   (spans removed + one `from .server_ops_parse import (...)` block at the
   `ParsedCommand` position). Symbol-precise so interleaved STAY symbols
   (`_SENSITIVE_QUERY_KEYS`, snapshot kinds, `_EXPLAIN_RESOURCE_DESCRIPTIONS`,
   `_is_dry_run`, `_preview`) are never swept.
3. **Byte-parity guard in the script** — assert every moved block appears
   verbatim in the leaf and is absent from the new server_ops.py; assert
   the re-import lists all 26 names.
4. **Import smoke** — `python -c "import anomaly_metric_creator.server;
   import anomaly_metric_creator.server_ops; import
   anomaly_metric_creator.server_mcp"` resolves (catches any missed
   staying reference as a NameError/ImportError).
5. **Oracle diff** — re-run the oracle → `candidate.json`; `diff
   baseline.json candidate.json` must be empty (behavior-identical).
6. **Splice-hazard grep** — `grep -nE "^from \." server_ops.py`: confirm
   only the profiles + parse re-import stubs and the mutations/traces
   imports remain; no orphaned fragment.
7. **Targeted tests** —
   `.venv/bin/pytest tests/test_server.py tests/test_server_ops_fuzz.py
   tests/test_server_mcp.py tests/test_server_eval_mode.py -n 0`.
8. **Full suite** — `.venv/bin/pytest`.
9. **Lint** — `.venv/bin/pre-commit run --all-files` (fixes blank-line
   drift; mypy gate).
10. **Docs/spec** — add `server_ops_parse.py` to the CLAUDE.md server-module
    map and `.trellis/spec/amc/backend/architecture.md`; record measured
    `server_ops.py` end size + leaf size in the epic step tracker
    (`implement.md`) and the epic prd Child Tasks list.
11. **Ship** — `sd-ship until=merge` (nested work-loop context).

## Validation commands

```bash
python -c "import anomaly_metric_creator.server, anomaly_metric_creator.server_ops, anomaly_metric_creator.server_mcp"
.venv/bin/python <scratch>/parse_oracle.py --capture > <scratch>/candidate.json
diff <scratch>/oracle_baseline.json <scratch>/candidate.json && echo ORACLE-IDENTICAL
.venv/bin/pytest tests/test_server.py tests/test_server_ops_fuzz.py tests/test_server_mcp.py tests/test_server_eval_mode.py -n 0
.venv/bin/pytest
.venv/bin/pre-commit run --all-files
wc -l src/anomaly_metric_creator/server_ops_parse.py src/anomaly_metric_creator/server_ops.py
```

## Review gates

- Leaf < 800 lines; zero `server_ops` imports in the leaf.
- Oracle diff empty; server suites green.
- Facades / `server.py` alias block / `server_mcp.py` imports unchanged
  (git diff touches only the two module files + docs + task artifacts).

## Rollback

Single squash-mergeable branch; revert the branch. No data/schema/CLI
surface change, so revert is clean.
