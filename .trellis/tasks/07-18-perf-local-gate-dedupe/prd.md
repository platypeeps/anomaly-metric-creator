# Remove duplicate work from the local review gate

## Goal

Two thirds of the deterministic local gate is one 41-line script doing work
that pre-commit and `full-check` already did. Separately, Prism runs three
times by default and the KB freshness check fails instead of self-healing a
gitignored artifact, costing a manual regen plus a full re-run of every step
before it.

## Measurement context

Deterministic steps of the local gate, timed on this machine:

| Step | Time |
|---|---|
| `scripts/check-review-preflight.mjs` | **4.19s** |
| `scripts/sd-ai-command-pack-install-audit.py` | 1.06s |
| `scripts/sd-ai-command-pack-review-preflight.mjs` | 0.54s |
| KB `--check` | 0.24s |
| `scripts/sd-ai-command-pack-pr-body-scope.py` | 0.10s |
| `scripts/sd-ai-command-pack-review-scope.sh` | 0.09s |
| `scripts/classify-ci-changes.sh` | 0.01s |
| **Total** | **~6.3s** |

`check-review-preflight.mjs` is 66% of that, and every one of its four steps
is already covered:

- line 25 `tools/check_ci_review_contract.py` — already a pre-commit hook
  (`.pre-commit-config.yaml:127-132`)
- line 26 `tools/check_copilot_instruction_contract.py` — already a hook
  (`.pre-commit-config.yaml:137-142`)
- line 27 `scripts/sd-ai-command-pack-pr-body-scope.py` — **run a second
  time in the same gate** at `scripts/sd-ai-command-pack-full-check.sh:894`
- lines 28-40 — ~4.0s of pytest over 113 tests that test the lint scripts,
  which pre-commit already ran against the actual changed files

Both preflights run: `full-check.sh:860-868` invokes `$script` *and*
`$legacy_script` when both exist and differ. **They are not duplicates of
each other** — the pack preflight validates review *references* (path
existence, documentation guards, git diff analysis); the legacy one runs
contract guards and lint tests. Removing the legacy one loses nothing
because of the coverage above, not because the pack one replaces it.

The legacy script is also **self**-redundant: lines 25-26 invoke the two
contract guards directly, and lines 28-40 run the test files that invoke
those same guards against the same repo root
(`tests/test_ci_review_contract.py:232` and
`tests/test_copilot_instruction_contract.py:196` are each a
`test_real_repo_contract_is_clean` that runs the script against
`REPO_ROOT`). It does the work twice inside its own 4.19s.

### It cannot simply be deleted

`tools/check_copilot_instruction_contract.py` pins the file's existence
*and* its contents:

- `:32` — `REQUIRED_FILES["review_preflight"]`, and `check()` at `:404`
  reads every `REQUIRED_FILES` entry; a missing file is a violation.
- `:378` `_check_review_preflight_wiring` — requires the file to mention
  `tools/check_ci_review_contract.py`,
  `tools/check_copilot_instruction_contract.py`,
  `scripts/sd-ai-command-pack-pr-body-scope.py`,
  `tests/test_copilot_instruction_contract.py`, and
  `tests/test_pr_body_scope_lint.py`.

So the guard mandates the redundancy: the preflight must invoke the guard
that requires the preflight to invoke it. `tools/check_ci_review_contract.py`
adds three softer pins (`:270`, `:298`, `:330`) — `_require_contains` string
checks against the classifier, `full-check.sh`, and the pack docs, which
survive the file's deletion but must be unwound for a clean removal.

Deleting outright is therefore a ~15-file atomic change across both guards,
four test files' fixtures, and six doc/spec references — two of which
(`docs/SD_AI_COMMAND_PACK.md`, `.agents/skills/sd-full-check/SKILL.md`) are
pack-managed and would drift.

## Ownership constraint (read before editing)

Per `.sd-ai-command-pack/provenance.json` (pack 0.15.6), these are
**pack-managed** — local edits drift from provenance and are clobbered on
upgrade:

- `scripts/sd-ai-command-pack-full-check.sh`
- `scripts/sd-ai-command-pack-review-preflight.mjs`
- `scripts/sd-ai-command-pack-update-spec-kb.py`

`scripts/check-review-preflight.mjs` is **repo-local**, but it is pinned by
two repo-local contract guards (see "It cannot simply be deleted" above), so
it is not free to remove.

## Requirements

- **Trim `scripts/check-review-preflight.mjs` rather than delete it.** The
  cost is concentrated in one place: lines 28-40 spend ~4.0s of the script's
  4.19s running 9 lint-test files that the CI quick lane already runs as a
  superset (`ci.yml:260-269` runs all 9 plus `tests/test_server.py`). The
  three script invocations on lines 25-27 cost 0.16s combined.
  `_check_review_preflight_wiring` only requires the file to *mention*
  `tests/test_copilot_instruction_contract.py` and
  `tests/test_pr_body_scope_lint.py` — not to run all nine. Trimming the
  pytest block to those two files (or referencing them in a comment and
  dropping the block) takes the script to ~0.2s with **no contract change,
  no pack drift, and a single-file diff**. That is the same win as deletion
  at a fraction of the blast radius.
- Do not delete the file in this task. If full removal is wanted, it is a
  separate change that must also unwind `REQUIRED_FILES`, the wiring guard,
  the three `_require_contains` pins, four test files' fixtures, and the
  pack-managed doc references — and it should be raised upstream, since the
  pack's own contract guards are what mandate the redundancy.
- Confirm the trim leaves no gap for a contributor who has never run
  `pre-commit install`. Verified during analysis: every CI lane covers the
  removed checks — the lightweight lane runs both contract scripts directly
  (`ci.yml:213-215`), and the quick and full lanes both run the two
  `test_real_repo_contract_is_clean` tests, which execute those scripts
  against the real repo root.
- **Prism runs three times** (`full-check.sh:295-331`): unstaged (`:311`),
  staged (`:314`), and `merge_base..HEAD` (`:330`). On a branch with
  uncommitted work the committed-range pass largely re-covers the same
  hunks. This is pack-managed, so the in-repo action is documentation —
  record that `SD_AI_COMMAND_PACK_FULL_CHECK_PRISM=0` is the fast path and
  when it is appropriate. A behavior change belongs upstream in the pack.
- **KB gate self-heal**: `.obsidian-kb/` is gitignored (`.gitignore:16-20`)
  and `--check` costs 0.24s, so regeneration has zero working-tree effect —
  yet `full-check.sh:442-445` exits 1 at step 7 of 14. This reproduces on
  any `git pull` that touches a spec source. Pack-managed, so propose the
  self-heal upstream and document the local regen command meanwhile.
- Do not edit pack-managed files in place. If a change there is necessary,
  the task is to file it upstream and record the pending change here.

## Acceptance criteria

- [ ] The deterministic local gate drops below ~2.5s, measured before and
      after, with the timings in the PR description.
- [ ] No check is lost for a contributor who has never run
      `pre-commit install`; the PR names where each removed check now lives.
- [ ] Pack-managed files are byte-identical to their provenance entries
      after the change (`sd-ai-command-pack-install-audit.py` reports no
      drift).
- [ ] Any upstream-pack change is filed and referenced by this task rather
      than applied locally.
- [ ] The Prism opt-out and the KB regen command are documented where a
      developer hitting the interrupt will find them.

## Non-goals

- Changing pre-commit hook scope. All 14 hooks were reviewed and are
  correctly scoped: only `branch-name` is `always_run` (correctly, on the
  `pre-push` stage), every `pass_filenames: false` hook is gated by a
  `files:` regex, and no hook shells out to `gh` or spawns a subprocess per
  file. There is nothing to win there.
