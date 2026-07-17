# Close CI cadence and lint-mirror gaps

## Audit context

- **Source:** 2026-07-17 full repo audit @ b0df00b — report:
  `.trellis/audit/report-2026-07-17.md`; per-item evidence + fix sketches:
  `.trellis/audit/ledger.md`.
- **Ledger items:** A-047 (P2·S), A-048 (P2·S), A-049 (P2·S), A-050 (P3·S), A-051 (P3·S), A-052 (P3·S), A-053 (P3·S), A-060 (P3·S), A-061 (P3·S), A-062 (P3·S), A-063 (P3·M), A-065 (P3·M)

## Goal

A cluster of CI/tooling gaps: a labeled-event hole defeats the auto-merge full-gate
guarantee; the mypy gate exists only in YAML; pack refreshes burn the full matrix;
several lints have no CI mirror; and the guards lane runs unpinned Python.

## Scope (ledger items)

- A-047 — honor PR_AUTO_MERGE in ci.yml's labeled arm; pin with a check_ci_review_contract anchor + mutation test.
- A-048 — move the 19-module mypy gate list into tools/check_mypy_gate.py shared by CI and local preflight; mention in DEVELOPMENT_CYCLE.
- A-049 — classify .sd-ai-command-pack/* as review-tooling; add toolchain.sh (+shell lib) to both bash -n lists; widen py-syntax globs to scripts/*.py.
- A-050 — python3 fallback (or required mode) for full-check when node is absent.
- A-051 — pass --force-app to the classifier on workflow_dispatch.
- A-052 — extend the role-name live-tree scan roots to src/, scripts/, .agents/, .trellis/.
- A-053 — setup-uv + `uv run --python 3.14` for the lightweight guards step.
- A-060 — add role-name/amc-module-load/agent-hook-exceptions invocations to the CI guards step.
- A-061 — run check_branch_name.py against github.head_ref in the changes job.
- A-062 — wire the role-name lint at the commit-msg stage.
- A-063 — scheduled pack-sync workflow opening a PR only on change (reuse the auto-merge gate).
- A-065 — windows-latest collect-only lane (uv sync + pytest --collect-only).

## Acceptance criteria

- [ ] Labeled-event mutation test proves an armed PR re-gates on the full matrix.
- [ ] A pack-refresh diff classifies lightweight; toolchain.sh syntax-gated.
- [ ] Each added mirror/lint has a test or contract anchor.
- [ ] Closing PR flips each covered ledger item to `status: fixed` in
      `.trellis/audit/ledger.md` (same-PR, per ledger rules).

## Added by 2026-07-17 audit follow-up (session-discovered, no ledger id)

`scripts/classify-ci-changes.sh` `is_lightweight_path()` lightweight-classifies
`.trellis/spec/*`, `.trellis/tasks/*`, and `.trellis/workspace/*` (line ~106)
but **not** `.trellis/audit/*`. Audit-artifact-only PRs (the report + ledger
this audit produced) therefore fall out of the lightweight lane on path
classification alone. This rides alongside A-049 (the sibling classifier gap
for `.sd-ai-command-pack/*`): add `.trellis/audit/*` to the same
lightweight-path case in the same pass.

Note the diagnostic subtlety this correction records: an *auto-merge-armed* PR
runs the full matrix regardless of paths (ci.yml `PR_AUTO_MERGE` / the
`auto_merge_enabled` event force `full_ci_requested=true` — "auto-merge never
lands on quick-lane evidence"), so this classifier line only changes the cost
of an audit-doc PR that is **not** armed for auto-merge. It is a correctness
tidy of the classifier, not the reason any armed PR paid the full matrix.

Acceptance addendum:
- [ ] `.trellis/audit/*` is lightweight-classified alongside the other
      `.trellis/*` non-code paths, with a `tests/` assertion mirroring the
      existing classifier cases.
