# Registry-couple the MCP wall leak sweeps — Implementation Plan

## Execution Order

1. Branch from `main`. Read the current sweep
   (tests/test_server_eval_mode.py:188) and the `MCP_TOOLS` registry
   (server_mcp.py:793) side by side; list the 7 uncovered tools.
2. Build `_TOOL_MINIMAL_ARGS` for all 15 tools. Use `kind` for
   `kubectl_get`, `server.DEFAULT_RELEASE` for Helm, the first registered
   component metric for histogram calls, and resolve snapshot-dependent pod
   names inside the test before the call loop.
3. Add the coupling assertion (`set(table) == {t.name for t in MCP_TOOLS}`)
   ahead of the loop; rewrite the eval sweep to iterate the table; keep the
   slug + description assertions and non-empty guards; add the
   no-unexpected-`isError` assertion; fix the docstring.
4. Add the non-eval positive control test (same table, `eval_mode=False`,
   assert ≥1 active slug appears) so the sweep cannot pass vacuously.
5. Add the A-001 AST source-scan guard over each `MCP_TOOLS` handler and its
   transitively called module-local helpers, with the forbidden-access list
   and two-log-tool allowlist from `design.md`. Mutation-check both new tests
   (design.md Validation) before finalizing.
6. Update `.trellis/spec/amc/backend/api-cli-server.md` first with the
   registry-coupled sweep/structural-guard contract, mirror the concise
   new-tool instruction in `CLAUDE.md`, and flip A-021/A-001 to `fixed` in
   `.trellis/audit/ledger.md`.
7. Draft PR → pre-PR checklist → ready → merge.

## Validation Plan

```bash
.venv/bin/pytest tests/test_server_eval_mode.py tests/test_server_mcp.py -n 0
.venv/bin/pytest          # full suite
.venv/bin/pre-commit run --all-files
```

Mutation checks (run locally, then revert):
- drop one table entry → coupling assertion fails naming the tool;
- insert `_ = state.active_scenarios` into a handler → guard test fails.

## Documentation And Spec Updates

- Canonical Trellis API/server spec: registry-coupled sweep plus structural
  guard and the narrow gated-log exception, with source citations.
- `CLAUDE.md` MCP section: one mirrored new-tool sentence.
- Ledger flips in the same PR.

## Review Notes

- The positive control is the reviewer-sensitive point: without it, a
  serializer change that empties the blob passes both leak assertions.
  Call that out in the PR description.
- Test-resource cost: reuse the module's existing `_build_state` helper and
  tmp_path-scale runs; no session-scoped GB fixtures needed.

## Follow-Ups

- Narrowed investigation-view state for handlers — recorded as a possible
  future hardening (design.md non-goal), only if the source-scan guard
  proves insufficient.
