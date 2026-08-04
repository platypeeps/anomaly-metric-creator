# Implement: fix the `knowledge.obsidian-kb` false-block

Design is in `design.md` (Option A — advisory only for an **external**-symlinked
KB; in-repo symlinks and tracked directories keep blocking).
Root cause is in `research/root-cause.md`. This plan sequences the two-repo
delivery: the code fix is an **upstream** change in
`platypeeps/sd-ai-command-pack`; this repo's PR carries only the runbook note
and finalized task artifacts.

## Boundaries

- **Upstream repo** (`platypeeps/sd-ai-command-pack`, a local clone at
  main / 0.64.5, viewer ADMIN): the `kb_freshness_row` change + unit tests are
  prepared and tested **locally**. Opening the upstream PR is an irreversible,
  every-consumer-affecting action, so — matching `design.md`'s "Held for attended
  go-ahead" — it is held for explicit greenlight on the concrete diff. The prior
  in-session "Approve upstream PR" selection expressed intent to fix upstream;
  the concrete diff is shown and the push re-confirmed before it lands.
- **This repo** (`anomaly-metric-creator`): the `.trellis/spec` runbook note
  (AC2 posture + AC3 workaround), plus this task's artifacts. Ships via the
  authoritative green GitHub gate; the `knowledge.obsidian-kb` row is the known
  false-negative and is bypassed only for the local housekeeping gate, never a
  GitHub/CI protection.

## Ordered steps

### A. Upstream code fix (`platypeeps/sd-ai-command-pack`)

1. Branch off `main` in the upstream clone (non-`ver-*` name).
2. Edit `scripts/sd-ai-command-pack-check.py`: add the pure helper
   `_is_external_symlink(kb_root, repo)` (symlink whose `resolve(strict=False)`
   target is not the repo root and not under it), and in `kb_freshness_row`,
   after the existing `command_row(... --check)` call, add the advisory
   downgrade — `if _is_external_symlink(kb_root, repo) and row.get("status") ==
   "failed":` return a non-blocking `skipped` row carrying the drift in its
   diagnostic and the original `remediation`/`command`/`exitCode`/`durationMs`.
   An in-repo symlink or a tracked directory keeps blocking. Single function
   plus one helper; no aggregator/status-vocabulary change.
3. Add unit tests (5 cases from `design.md`): (a) no KB → `skipped`;
   (b) external-symlink KB whose `--check` fails → advisory `skipped` with drift
   in diagnostic; (c) external-symlink KB whose `--check` passes → `passed`;
   (d) real non-symlink tracked KB whose `--check` fails → still `failed`
   (the existing `test_stale_kb_is_reported_without_refresh_or_provider_dispatch`);
   (e) in-repo symlink whose `--check` fails → still `failed` (closes the
   `is_symlink()`-alone hole). Assert the aggregate `sd-check` status is not
   `failed` for (b) but is for (d) and (e). Add a direct
   `_is_external_symlink` unit test (external / in-repo / non-symlink / broken).
4. Run the pack's own suite (`pytest` in the upstream clone) — no regressions.
5. Show the diff + green test output, then **hold for explicit greenlight**
   before opening the upstream PR (irreversible, every-consumer-affecting).
   Only after re-confirmation, open the PR through the pack's CI.

**Validation (upstream):** the 4 new unit tests pass; full pack `pytest` green;
diff confined to `kb_freshness_row` + the new test.

### B. In-repo runbook note + artifacts (this task's PR)

6. Write a short runbook note under `.trellis/spec` capturing: the advisory
   posture decision (AC2) and the merge-via-green-GitHub-gate workaround (AC3)
   in force until the upstream fix syncs in. Point at `research/root-cause.md`
   for the mechanism.
7. Finalize task artifacts (this `implement.md`, keep `research/` + `design.md`).
   Note in the runbook that the prd's original `/tmp`-snapshot hypothesis was
   refuted by research (superseded by the live-external-vault mechanism).
8. Establish the feature branch, `task.py start`, commit the in-repo deliverables.
   **Merge path (bootstrap constraint):** the fix is not yet synced into this
   repo's vendored `scripts/`, so the coordinator's `knowledge.obsidian-kb` row
   still false-blocks `sd-review scope=pr` and the `sd-housekeeping` gate — a
   plain `sd-ship until=merge` cannot cleanly complete. This in-repo PR
   (docs/task-artifacts only) therefore merges via the **attended authoritative
   green GitHub gate** (`CI Result` pass + conversation resolution +
   `mergeStateStatus: CLEAN`), the exact documented workaround this task's
   runbook note captures and the same path used for PR #316 / #324. That merge
   is an operator judgment call (it bypasses only the local KB false-negative,
   never GitHub branch protection or CI), so it is surfaced for greenlight
   rather than auto-merged by the loop.

**Validation (in-repo):** `sd-check --json` shows `knowledge.obsidian-kb` no
longer the sole blocker once the patched helper is installed (post-sync);
pre-sync, confirm the GitHub `CI Result` gate is the authoritative green merge
path. No code under `src/` changes, so the Python suite is unaffected — the
in-repo diff is docs/task-artifacts only.

## Rollback

- Upstream: close the PR / revert the branch; the advisory downgrade is a single
  guarded branch, trivially removable.
- In-repo: the runbook note is additive docs; revert the commit if needed.

## Acceptance criteria coverage

- AC1 (root cause confirmed with a reproducible sequence + written explanation):
  `research/root-cause.md` — the `_run_check`/`kb_freshness_row`/`check_current`
  code trace, the refutation of the `/tmp`-snapshot hypothesis, and the
  non-deterministic count-swing evidence table. (The stale "438/441" figure is
  reconciled in `prd.md` as the superseded PR #316 observation.) Met.
- AC2 (working-tree-vs-advisory decision + rationale): `research/root-cause.md`
  posture section + `design.md` + the runbook note. Met.
- AC3 (upstream fix filed with approval OR local workaround captured): the
  **local workaround** (the runbook note, step 6) is the AC3-satisfying
  deliverable that lands in this repo's PR — it does not depend on the upstream
  PR. The upstream fix is prepared + tested (steps 1–4) and **held for greenlight**
  before filing (step 5); it satisfies AC3's first arm once filed, but AC3 is
  already met by the captured workaround regardless.
- AC4 (no regression to the real merge gate): the fix downgrades only the
  environment-dependent KB row; `CI Result` + conversation resolution stay
  authoritative. Preserved by construction.
