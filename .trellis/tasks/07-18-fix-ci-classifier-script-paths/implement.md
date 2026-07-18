# CI classifier script paths — Implementation Plan

## Execution Order

1. Branch from `main`. Reproduce the enumeration so the PR rests on current
   data rather than this design's snapshot:
   ```bash
   for f in $(git ls-files scripts tools docs); do
     out=$(bash scripts/classify-ci-changes.sh -- "$f" | grep '^lightweight_only=' | cut -d= -f2)
     [ "$out" = "false" ] && echo "app_required: $f"
   done | sort
   ```
2. **Verify Group A has no test coverage** before classifying anything. For
   each of the 8 candidates:
   ```bash
   rg -n "sd_ai_command_pack_lib|sync-agent-skills|update_repomix|record-session|review-learnings|shell-lib|toolchain|update-spec-kb" tests/
   ```
   Any member that a test exercises drops out of Group A and stays
   app-required. `scripts/sd_ai_command_pack_lib.py` is the likeliest —
   check it specifically against
   `tests/test_sd_ai_command_pack_full_check_script.py`.
3. Add `is_repo_tooling_path()` to `scripts/classify-ci-changes.sh` next to
   `is_review_tooling_path()` (`:70-94`), listing the verified Group A
   paths. Call it from `is_lightweight_path()` (`:96-116`) alongside the
   existing `is_review_tooling_path` call. Do **not** extend
   `is_review_tooling_path` — it also drives `review_tooling_changed`, and
   `scripts/update_repomix` is not review tooling.
4. Extend `tests/test_ci_change_classifier.py`:
   - one case per newly classified path asserting **both**
     `lightweight_only=true` and `app_required=false`;
   - a negative case per Group B sample (at least one `tools/check_*.py`)
     asserting `app_required=true`;
   - a negative case for `src/anomaly_metric_creator/*`;
   - a dependency case (`pyproject.toml`) and a workflow case asserting the
     escalation still fires;
   - a mixed-diff case (Group A path + application path) asserting
     escalation wins.
5. Record the Group B decision — including `tools/benchmark_combine.py`
   staying app-required despite having no test — in this task's `prd.md`, so
   it is not re-raised as an oversight.
6. Update `CLAUDE.md`'s CI-cadence section with the new predicate and the
   governing rule from `design.md`.
7. Draft PR -> pre-PR checklist (CI/workflow hygiene, completeness,
   test path determinism) -> ready -> merge.

## Validation Plan

```bash
# every Group A path is lightweight
for f in scripts/sd-ai-command-pack-record-session.py \
         scripts/sd-ai-command-pack-review-learnings.py \
         scripts/sd-ai-command-pack-shell-lib.sh \
         scripts/sd-ai-command-pack-toolchain.sh \
         scripts/sd-ai-command-pack-update-spec-kb.py \
         scripts/sd_ai_command_pack_lib.py \
         scripts/sync-agent-skills.py \
         scripts/update_repomix; do
  echo "$f -> $(bash scripts/classify-ci-changes.sh -- "$f" | grep -E '^(lightweight_only|app_required)=' | tr '\n' ' ')"
done

# Group B and application paths still escalate
bash scripts/classify-ci-changes.sh -- tools/check_role_name_leaks.py
bash scripts/classify-ci-changes.sh -- src/anomaly_metric_creator/legacy.py
bash scripts/classify-ci-changes.sh -- pyproject.toml
bash scripts/classify-ci-changes.sh -- .github/workflows/ci.yml

# mixed diff still escalates
bash scripts/classify-ci-changes.sh -- scripts/update_repomix src/anomaly_metric_creator/legacy.py

.venv/bin/pytest tests/test_ci_change_classifier.py -n 0
.venv/bin/pre-commit run --all-files
```

End-to-end confirmation is a real PR: a docs-plus-`scripts/update_repomix`
diff must select the lightweight lane, not the 16-minute matrix. That was
the original symptom, so it is the acceptance evidence.

## Documentation And Spec Updates

- `CLAUDE.md` CI-cadence section — the new predicate and the rule that a
  path is only lightweight if no test would be skipped.
- Inline comment in `classify-ci-changes.sh` explaining why `tools/` is
  deliberately excluded, so the next reader does not "complete" the fix by
  adding it.

## Review Notes

- Lead with the enumeration and the **two groups**. The obvious reviewer
  instinct is "why not classify all of `scripts/` and `tools/`?" — answer it
  before it is asked, with the concrete example: classifying
  `tools/check_role_name_leaks.py` lightweight would skip
  `tests/test_role_name_leaks_lint.py` on the PR that changed it.
- State the asymmetry explicitly: over-classification ships an untested
  regression; under-classification costs free CI minutes on a public repo.
  That is why the boundary is drawn conservatively.
- Confirm no pack-managed file was edited — several Group A members are
  pack-managed, but this change touches only the classifier.
- The completeness heading applies: the PR title names a class, so the
  description must show the class was enumerated, not just the one path that
  bit.

## Follow-Ups

- If the lightweight lane ever grows a "run these tests" step, revisit
  Group B: paths whose tests run in the lightweight lane could then be
  classified lightweight without losing coverage.
- `tools/benchmark_combine.py` has no test at all. Rather than reclassify
  it, consider whether it warrants a smoke test — that would close the gap
  in the more useful direction.
