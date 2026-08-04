# Implement — Extract k8s objects + tables (step 4, 3-leaf shape)

Seam decided (maintainer, 2026-08-04): **Option A**, add a shared
`server_ops_support.py` pure lower leaf so the object builders reach the
snapshot/label/timestamp/string/resource-version accessors downward,
never by reverse-importing `server_ops`.

## Module set + dependency order

```
server_mutations (DEFAULT_NAMESPACE, _mutation_resource_key, …)
      ↑
server_ops_support   ← DEFAULT_RELEASE, DEFAULT_CHART, _snapshot_row_namespace,
                        _snapshot_row_labels, _parse_user_timestamp,
                        _string_dict, _k8s_list_resource_version
      ↑            ↑
server_k8s_objects   server_ops (re-imports every moved name)
      ↑
server_k8s_tables
```

One-way rule: each leaf imports only stdlib, lower leaves, and (tables)
`server_k8s_objects`. `SimulationState` is annotation-only under
`from __future__ import annotations`, so no leaf imports it at runtime.

## Ordered checklist

1. [ ] Render-oracle `k8s_oracle.py`: build a live `SimulationState`
       (`server.build_state`), render the fixed `kubectl get`/`describe`
       corpus (pods/deployments/configmaps/secrets/events/services, with
       and without Table `Accept`, `-o yaml`/`-o json`), and snapshot the
       object + table + cell builder outputs to sorted JSON. Capture
       **baseline on main (pre-move)**.
2. [ ] Generalize the step-2 AST extractor → `extract_leaf.py`,
       parametrized by (MOVE_ORDER, LEAF path, header, downward-import
       names, annotation-only names, re-import position symbol). Keep the
       three self-checks: each symbol found once, residual-free-name safety
       (loud on any missed staying-helper dep), verbatim byte-identity.
3. [ ] Extract `server_ops_support.py` (7 symbols). server_ops re-imports
       at `DEFAULT_RELEASE`'s original position (@41).
4. [ ] Extract `server_k8s_objects.py` (per-kind builders + metadata /
       owner / label / container-state / timestamp / pod-ip helpers).
       Header imports the 5 support accessors + `DEFAULT_RELEASE`
       downward. Dispatcher `_k8s_objects_for_resource` and
       `_helm_secret_objects` STAY (steps 5 / 3).
5. [ ] Extract `server_k8s_tables.py` (Table + column + schema + per-kind
       cell builders). Imports `_k8s_list_resource_version` from support;
       imports `server_k8s_objects` only if the residual check demands it.
6. [ ] Splice-hazard grep: for each cut range, confirm no prior-extraction
       `^from \.` re-import stub was swept into the deletion; confirm every
       leaf re-import still resolves.
7. [ ] Each new module < 800 lines; `server_ops.py` measured.
8. [ ] Tests: `tests/test_server.py tests/test_server_ops_fuzz.py
       tests/test_server_mcp.py tests/test_server_eval_mode.py
       tests/test_server_watch.py -n 0`, then full suite + pre-commit.
9. [ ] Oracle candidate capture; byte-diff vs baseline → IDENTICAL.
10. [ ] Docs: CLAUDE.md server-module map + `.trellis/spec/amc/backend/
        architecture.md` list all three leaves; epic tracker records step 4
        done, measured server_ops size, and that
        `_k8s_metadata`/`_k8s_timestamp` now live in server_k8s_objects.py
        (helm's k8s deps resolved).

## Validation commands

```bash
python <scratch>/k8s_oracle.py > <scratch>/k8s_baseline.json   # on main, pre-move
python <scratch>/k8s_oracle.py > <scratch>/k8s_candidate.json  # post-move
diff <scratch>/k8s_baseline.json <scratch>/k8s_candidate.json && echo IDENTICAL
.venv/bin/pytest tests/test_server.py tests/test_server_ops_fuzz.py \
  tests/test_server_mcp.py tests/test_server_eval_mode.py \
  tests/test_server_watch.py -n 0
.venv/bin/pytest && .venv/bin/pre-commit run --all-files
```

## Rollback

Pure module split. Revert the branch; `server_ops.py` re-imports mean no
caller changed, so a revert restores the pre-move tree exactly. The
render-oracle byte-diff + fuzz corpus are the behavior-identity gate; any
diff aborts before PR.
