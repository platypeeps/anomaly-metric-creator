# Root cause: sd-review `knowledge.obsidian-kb` false-block

Confirmed 2026-08-04 (iteration 8, while shipping epic 07-06 step 4 / PR #324).
Evidence is read-only; no coordinator or repo state was mutated during this
investigation.

## Original prd hypothesis — REFUTED

The prd (written from the PR #316 observation) proposed: the coordinator
rebuilds/counts the KB from committed content in a `/tmp/sd-review-{source,target}-*`
snapshot, which excludes the gitignored working-tree `.obsidian-kb`, so it
undercounts.

Code trace disproves the snapshot part:

- `scripts/sd-ai-command-pack-review.py:664-676` (`_run_check`) — **no temp
  snapshot is created**. It runs `scripts/sd-ai-command-pack-check.py --repo <repo> --json`
  with `cwd=repo` against the live repository.
- `scripts/sd-ai-command-pack-check.py:927-952` (`kb_freshness_row`) — invokes
  `update-spec-kb.py --check` with `cwd=repo` (execute_check at ~:882).
- `scripts/sd-ai-command-pack-update-spec-kb.py` `check_current` (~:1517-1560) /
  `collect_copy_state` — counts present copies under the live `root/.obsidian-kb`
  (follows the symlink).

So the coordinator's KB check reads the **same live working tree** the standalone
`--check` does. There is no committed-snapshot undercount.

## Confirmed mechanism — live external artifact + non-deterministic gate

`.obsidian-kb` is an **absolute symlink to a live external Obsidian vault**:

- `ls -ld .obsidian-kb` -> `.obsidian-kb -> /path/to/<obsidian-vault>/raw/anomaly-metric-creator`
  (an absolute symlink whose target resolves outside the repo tree).
- gitignored: `.gitignore:19:/.obsidian-kb`; untracked (`git ls-files .obsidian-kb` empty).
- live working-tree file count follows the symlink into the vault.

The vault mutates continuously (Obsidian app, sync tooling), **independent of repo
HEAD**. The coordinator caches each `sd-check` verdict in a per-`(headOid,
configurationDigest)` state file under
`$TMPDIR/sd-ai-command-pack-501-*/review-controller/review-*.json`; for
`scope=pr` the identity's `worktreeDigest` is `None`
(`review.py:1706 worktree_digest = _worktree_digest(repo) if scope != "pr" else None`),
and even for non-pr scopes `_worktree_digest` (`:555-612`) is built from
`git ls-files` + `--exclude-standard` untracked, which **excludes gitignored
paths**. So the `.obsidian-kb` state can never enter the cache key.

### Direct evidence: the cached `knowledge.obsidian-kb` rows

58 cached KB rows across the review-controller state files show the copy count
swinging wildly across runs minutes apart, with the row failing at some counts
and passing at others:

| time (Aug 4) | check.status | kb.status | copies | note |
|---|---|---|---|---|
| 10:01 | failed | **failed** | 451 | — |
| 10:02 | failed | **failed** | 450 | "stale generated entries removed: 4" |
| 10:03 | passed | passed | 521 | — |
| 10:06 | failed | passed | 469 | (other check failed) |
| 10:09 | passed | passed | 454 | — |
| 10:14 | passed | passed | 469 | — |
| 10:47 | passed | passed | 521 | — |

Standalone `update-spec-kb.py --check` at investigation time: `copies: 452,
expected copies: 452` -> current/exit 0.

The count is not a stable baseline — it tracks whatever the external vault holds
at check time. When the vault is momentarily inconsistent (present != expected,
or transient "stale generated entries"), the KB row fails and the deterministic
`sd-check` gate returns blocked. A later run at a different vault state passes.
The standalone `--check` is never cached, so it always reflects the current
vault and typically reads clean.

## Why it blocks every ship

`sd-review scope=pr` and the `sd-housekeeping` merge gate both run this same
`sd-check`. Because the KB row can fail non-deterministically on the live vault
and its verdict is memoized against a key that excludes the artifact, a single
transient failure blocks the coordinator and survives KB rebuilds — reproducing
only under the coordinator, never under a fresh standalone `--check`. This forced
manual green-gate merges for PR #316 and PR #324.

## Posture decision (AC2)

A deterministic review/merge gate must not block on an artifact that is
**gitignored, external, live-mutating, and never shipped**. The KB freshness
check should be **advisory (non-blocking)** in the coordinator: it may report
`stale`/drift as information, but must not contribute a blocking verdict to
`sd-check`, and must never gate a GitHub merge whose authoritative `CI Result`
gate is green. (Reading "committed content" is not an option here — there is no
committed KB; the artifact is intentionally gitignored.)

This keeps the real gate intact (AC4): `CI Result` + conversation resolution
remain authoritative; only the environment-dependent KB row is downgraded.

## Narrowest fix locations (upstream `platypeeps/sd-ai-command-pack`)

The `scripts/*.py` here are **vendored** from `platypeeps/sd-ai-command-pack`
(synced by `.github/workflows/sd-ai-command-pack-sync.yml`). Any code fix is an
upstream change. Candidate sites, narrowest first:

1. `sd-ai-command-pack-check.py` `kb_freshness_row` — classify the
   `knowledge.obsidian-kb` row as advisory/non-blocking so a failing/`stale` KB
   state does not set the aggregate `check.status` to failed. Single-site,
   surgical.
2. `sd-ai-command-pack-review.py` check aggregation — treat the
   `knowledge.obsidian-kb` check id as informational when rolling checks up into
   the blocking verdict.

Preferred: option 1 (source of the row) so both the coordinator and any direct
`sd-check` consumer inherit the advisory posture.

## Local workaround (already in force, AC3)

Until the upstream fix lands: merge via the authoritative green GitHub gate
(`CI Result` pass + conversation resolution + `mergeStateStatus: CLEAN`) with
explicit approval, treating the coordinator's KB row as the verified
false-negative it is. Used for PR #316 and PR #324.
