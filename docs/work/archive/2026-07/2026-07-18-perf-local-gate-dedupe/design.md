# Local gate dedupe — Design (SD Work Designs, 2026-07-18)

## Overview

Three findings, only one of which is actionable inside this repository. The
actionable one removes the duplicate pytest bundle from the repo-local gate.
The other two live in pack-managed files and are upstream conversations.

The 2026-07-20 implementation pass reconciled this plan against command-pack
0.24.7. The preflight had gained the canonical mypy gate and a tenth pytest
file. Current component timing is 2.64s for pytest versus about 0.26s for the
four direct guards; the implementation preserves all four guards and replaces
the pytest invocation with comments naming the two contract-pinned test paths.

## Proposal

### Part A — trim `scripts/check-review-preflight.mjs` (actionable)

The original analysis measured this attribution:

| Lines | Work | Cost |
|---|---|---|
| 25 | `tools/check_ci_review_contract.py` | 0.032s |
| 26 | `tools/check_copilot_instruction_contract.py` | 0.034s |
| 27 | `scripts/sd-ai-command-pack-pr-body-scope.py` | 0.090s |
| 28-40 | pytest over 9 lint-test files | **~4.0s** |

All four are covered elsewhere, and the first two are covered *by the tests
the fourth step runs*:

- `tests/test_ci_review_contract.py:232` and
  `tests/test_copilot_instruction_contract.py:196` are each a
  `test_real_repo_contract_is_clean` that runs the guard against
  `REPO_ROOT`. So lines 25-26 and lines 28-40 do the same work twice inside
  one 4.19s script.
- `scripts/sd-ai-command-pack-pr-body-scope.py` runs again as its own
  `full-check` step (`run_sd_ai_command_pack_pr_body_scope_check` in
  `main()`).
- All 9 original test files were in the CI quick lane as a strict
  superset — it adds `tests/test_server.py`.

**The file cannot simply be deleted.** `tools/check_copilot_instruction_contract.py`
pins it twice: `:32` puts it in `REQUIRED_FILES`, and `check()` at `:404`
reads every entry (missing file -> violation); `:378`
`_check_review_preflight_wiring` reads its *contents* and requires it to
mention five paths. The guard mandates the redundancy — the preflight must
invoke the guard that requires the preflight to invoke it.

But `_check_review_preflight_wiring` requires only that the file **mention**
`tests/test_copilot_instruction_contract.py` and
`tests/test_pr_body_scope_lint.py` — not that it execute the pytest suite. So:

> Drop the duplicate pytest block and reference the two contract-pinned test
> paths in a comment.

Result: the expensive duplicate bundle is removed with **no contract change
and no pack drift**. Same functional win as deletion at a fraction of the
blast radius.

The two preflights are **not** duplicates of each other, contrary to the
first reading. The pack preflight validates review *references* — path
existence, documentation guards, git diff analysis. The legacy one runs
contract guards and lint tests. Removal is justified by CI coverage, not by
replacement.

### Part B — Prism triple invocation (upstream)

`run_prism_reviews` (`full-check.sh:295-331`) defaults to `auto`, and `auto`
is not in the disabled set (`:65-70`), so it runs — as three invocations:
unstaged (`:311`), staged (`:314`), and `merge_base..HEAD` (`:330`). On a
branch with uncommitted work the committed-range pass largely re-covers the
same hunks. Pack-managed; the in-repo action is to document
`SD_AI_COMMAND_PACK_FULL_CHECK_PRISM=0` as the fast path and when it is
appropriate.

### Part C — KB gate self-heal (upstream)

`.obsidian-kb/` is gitignored (`.gitignore:16-20`) and `--check` costs
0.244s, so regeneration has zero working-tree effect — yet
`full-check.sh:442-445` exits 1 at step 7 of 14, forcing a manual regen plus
a re-run of the six preceding steps. This reproduces on any `git pull` that
touches a spec source; it did so during the analysis that produced this
task. Pack-managed; propose self-heal upstream, document the regen command
meanwhile.

## Boundaries And Non-Goals

- **Do not edit pack-managed files**: `full-check.sh`,
  `sd-ai-command-pack-review-preflight.mjs`,
  `sd-ai-command-pack-update-spec-kb.py`
  (`.sd-ai-command-pack/provenance.json`, pack 0.24.7). Local edits drift
  and are clobbered on upgrade.
- **Do not delete `scripts/check-review-preflight.mjs`** in this task. Full
  removal is a ~15-file change across both contract guards, four test files'
  fixtures, and six doc references — two of which
  (`docs/SD_AI_COMMAND_PACK.md`, `.agents/skills/sd-full-check/SKILL.md`)
  are pack-managed.
- No pre-commit hook changes. All 14 were reviewed and are correctly
  scoped: only `branch-name` is `always_run` (on `pre-push`), every
  `pass_filenames: false` hook is gated by a `files:` regex, and none shells
  out to `gh` or spawns a subprocess per file.

## Affected Files

`scripts/check-review-preflight.mjs` (Part A), `docs/DEVELOPMENT_CYCLE.md`
(Prism opt-out, KB regen command), this task's `prd.md` (upstream items).

## Risks And Edge Cases

- **The trim must keep the two pinned test-file mentions.** Dropping them
  fails `_check_review_preflight_wiring`, which runs in the CI lightweight
  lane, in pre-commit, in both `test_real_repo_contract_is_clean` tests, and
  in the script itself. A mistake here fails four ways at once — noisy, but
  confusing to diagnose.
- **Coverage for a contributor who never ran `pre-commit install`** was the
  open question; it is closed. Every CI lane covers the removed checks: the
  lightweight lane runs both contract scripts directly (`ci.yml:213-215`),
  and the quick and full lanes both run the two real-repo contract tests.
- **Upstream items may never land.** Parts B and C should be filed and
  referenced, not blocked on. The documentation half is deliverable
  immediately.

## Validation

- Time the gate before and after; the repo-local preflight dropped from a 2.92s
  warm median to 0.32s, and the deterministic component total measured 2.40s.
- Run the trimmed preflight directly and confirm exit 0.
- Run `tools/check_copilot_instruction_contract.py` and
  `tools/check_ci_review_contract.py` against the real repo — both must
  stay clean, proving the pins are still satisfied.
- `sd-ai-command-pack-install-audit.py` must report no pack drift.
