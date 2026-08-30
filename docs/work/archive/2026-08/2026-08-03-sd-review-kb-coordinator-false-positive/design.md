# Design: fix the `knowledge.obsidian-kb` false-block

Root cause is confirmed in `research/root-cause.md`: the deterministic
`sd-check` `knowledge.obsidian-kb` row runs `update-spec-kb.py --check` against a
live `.obsidian-kb` that, in this repo, is a **symlink to an external Obsidian
vault** mutating independently of repo HEAD. The row therefore fails
non-deterministically (present != expected mid-vault-edit), and because the
coordinator's state key excludes gitignored/untracked paths
(`review.py:1706`, `worktreeDigest=None` for `scope=pr`), a transient failure
blocks `sd-review`/`sd-housekeeping` and never clears against a live re-check.

## Boundary

The `scripts/*.py` in this repo are **vendored** from
`platypeeps/sd-ai-command-pack` (installed by
`.github/workflows/sd-ai-command-pack-sync.yml`). The code fix is an upstream
change in that repo (`platypeeps/sd-ai-command-pack`, a local clone at
main / 0.64.5, viewer permission ADMIN). This repo only receives it later via
the sync PR.

The fix is confined to one function:
`scripts/sd-ai-command-pack-check.py::kb_freshness_row` (upstream lines
927-952). That function already returns non-blocking rows for two cases —
`skipped` when no `.obsidian-kb` exists, `unavailable` when the helper is
missing — and only reaches the blocking `command_row(... --check)` path when the
KB is present with a real helper.

## Discriminator

`kb_root.is_symlink()` alone is **not** a safe discriminator: a repo could carry
a symlinked `.obsidian-kb` whose target is *inside* the repo (committed via the
link), and downgrading it would silently drop a legitimate gate. The precise
invariant behind the false-block is that the KB is an **external symlink** — a
symlink whose resolved target escapes the repository root (the live external
vault). The discriminator is therefore `_is_external_symlink(kb_root, repo)`:

```python
def _is_external_symlink(kb_root: Path, repo: Path) -> bool:
    """True iff kb_root is a symlink whose resolved target is outside repo."""
    if not kb_root.is_symlink():
        return False
    target = kb_root.resolve(strict=False)  # tolerate a broken link
    repo_root = repo.resolve()
    return repo_root != target and repo_root not in target.parents
```

This splits the KB shapes correctly:

- **External-symlinked `.obsidian-kb`** (target escapes the repo) — the live,
  gitignored, never-shipped vault. Its freshness is non-deterministic and cannot
  be a deterministic gate. THIS is the false-block source; downgrade it.
- **In-repo symlink** (target resolves under the repo) — deterministic against
  HEAD; keeps blocking.
- **Real tracked/committed `.obsidian-kb` directory** (a consumer repo that
  commits its KB) — deterministic against HEAD; a freshness failure there is a
  genuine, actionable drift and keeps blocking.
- **Broken link** — `resolve(strict=False)` yields the declared target path;
  an external broken link downgrades (it is external and non-shippable anyway),
  an in-repo broken link keeps blocking so the breakage surfaces.

## Options

### Option A — advisory only for an external-symlinked KB (recommended)

In `kb_freshness_row`, always run `--check` (so the drift is reported), but when
the KB is an **external symlink** (`_is_external_symlink`, above) map a
**failed** result to a non-blocking row using the existing `skipped` status,
with the drift preserved in the diagnostic and the original `command` /
`exitCode` / `durationMs` carried through for full diagnostics. A passing check
still returns `passed`. An in-repo symlink or a real tracked directory keeps
today's blocking behavior unchanged.

Shape (single function plus one small pure helper — no aggregator or
status-vocabulary change; reuses the existing non-blocking `skipped` status,
which is absent from `AGGREGATE_PRECEDENCE` so it never contributes to the
blocking verdict):

```python
def kb_freshness_row() -> dict[str, object]:
    kb_root = repo / ".obsidian-kb"
    if not (kb_root.exists() or kb_root.is_symlink()):
        return _result_row("knowledge.obsidian-kb", "builtin", "skipped", ...)
    helper = repo / "scripts/sd-ai-command-pack-update-spec-kb.py"
    if not helper.is_file() or helper.is_symlink():
        return _result_row("knowledge.obsidian-kb", "builtin", "unavailable", ...)
    row = command_row("knowledge.obsidian-kb", (sys.executable, str(helper), "--check"), remediation=...)
    # Advisory downgrade: an external symlinked vault is non-deterministic and
    # gitignored/never-shipped, so its freshness must not gate a merge.
    if _is_external_symlink(kb_root, repo) and row.get("status") == "failed":
        return _result_row(
            "knowledge.obsidian-kb", "builtin", "skipped",
            diagnostic="advisory: external-symlinked .obsidian-kb drift is non-deterministic and never shipped; " + str(row.get("diagnostic", "")),
            remediation=row.get("remediation"),
            exit_code=row.get("exitCode"),
            command=row.get("command"),
            duration_ms=int(row.get("durationMs") or 0),
        )
    return row
```

Pros: minimal blast radius; preserves the gate where it is meaningful (committed
KB *and* in-repo symlinks); matches the root cause exactly (external target); no
new status vocabulary or `review.py` aggregation change. Cons: introduces one
guarded branch plus a small pure helper; an external-symlinked KB can no longer
*ever* block on drift (acceptable — it is gitignored and never shipped, so drift
has no downstream consumer).

### Option B — advisory always

Make the `knowledge.obsidian-kb` row never contribute to the blocking verdict
regardless of KB shape (e.g. always downgrade a failure to `skipped`, or teach
`review.py` to treat the check id as informational).

Pros: even simpler; one predicate. Cons: removes the freshness gate for consumer
repos that legitimately commit their `.obsidian-kb`; broader behavior change for
shared tooling. Rejected unless the maintainer prefers no KB gate anywhere.

## Recommendation

**Option A.** It fixes the confirmed non-determinism at its source (the external
symlink), keeps the gate for deterministic committed KBs, and is a single-function
change with no status/aggregation ripples.

## Test plan (upstream repo)

- Unit: `kb_freshness_row` with (a) no KB -> `skipped`; (b) **external**-symlink
  KB (target outside repo) whose `--check` fails -> non-blocking advisory
  `skipped` with drift in diagnostic; (c) external-symlink KB whose `--check`
  passes -> `passed`; (d) real (non-symlink) tracked KB whose `--check` fails ->
  still `failed` (gate preserved — the existing
  `test_stale_kb_is_reported_without_refresh_or_provider_dispatch`); (e)
  **in-repo** symlink (target resolves under the repo) whose `--check` fails ->
  still `failed` (gate preserved — closes the `is_symlink()`-alone hole).
  Assert the aggregate `sd-check` status is not `failed` for (b) but is for (d)
  and (e). Also unit-test `_is_external_symlink` directly for external / in-repo
  / non-symlink / broken-link inputs.
- Run the pack's own suite (`pytest` in the upstream clone) — no regressions.
- Manual: run `sd-check --json` in anomaly-metric-creator with the patched
  helper installed and confirm `knowledge.obsidian-kb` no longer blocks while
  the vault is mid-edit.

## Rollout

- Upstream PR to `platypeeps/sd-ai-command-pack` (Option A), through its own CI.
- After it releases, this repo picks it up via the existing
  `sd-ai-command-pack-sync` automation PR — no manual vendored-file edit here.
- AC4 preserved: `CI Result` + conversation resolution stay the authoritative
  merge gate; only the environment-dependent KB row is downgraded.

## In-repo deliverables (this task's PR)

- `research/root-cause.md` (done) + this `design.md`.
- A short runbook note in `.trellis/spec` (or the SD runbook) capturing the
  merge-via-green-gate workaround until the upstream fix lands (AC3), and the
  advisory-posture decision (AC2).
- Task artifacts updated; ship via the green GitHub gate (KB row is the known
  false-negative).

## Held for attended go-ahead

Implementing + testing the shared-tooling change and opening the upstream PR are
held for an attended step: the change alters gate behavior for every repo
consuming the pack, and the maintainer chose to review the concrete diff before
the push. This design fixes the exact shape to approve.
