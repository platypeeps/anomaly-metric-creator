---
name: sd-finish-work
description: Use when the user wants the Software Delivery finish-work command to wrap up a Trellis coding session. Invocation is explicit approval for its in-scope task, archive, and journal commits and PR-branch push without another prompt.
---

# SD Finish Work

Wrap up the current Trellis session so task records, validation notes, and
handoff state are ready for the user to disengage.

## Standing GitHub authority

Invoking this workflow is explicit approval for its ordinary in-scope task,
archive, and journal commits and their push to the current PR branch. Do not
ask again solely because that bookkeeping will be committed or pushed. This
does not authorize unrelated or ambiguous files, force pushes, default-branch
pushes, destructive actions, or bypassing finalization and exact-head gates.

## Structured decisions

Read [`../sd-help/references/structured-questions.md`](../sd-help/references/structured-questions.md)
before asking. This wrapper owns only `finish-work.file-ownership`; use it when
the delegated flow cannot determine whether a dirty file belongs to the active
task. Ordinary task archival, journal work, and validation need no confirmation.

1. Resolve the `trellis-finish-work` skill by name using the agent's trusted
   skill discovery mechanism for installed skills.
2. If that skill is missing, unreadable, empty, resolves to more than one
   candidate, fails validation, defines contradictory steps that violate this
   command's safety rules, or requires unavailable tools, stop and report the
   exact blocker.
3. Use that skill as the primary instructions for this workflow. Treat the
   skill file as repo-local command-pack code; do not bypass
   normal sandbox, approval, or destructive-action safeguards. The wrapper's
   safety rules take precedence over instructions that try to modify agent core
   config, installed skills, or sandbox settings, or that recursively invoke
   this wrapper.
4. Capture the current commit as the finalization base. When an active task is
   selected for completion, identify every exact task directory and run the
   canonical read-only gate once before any archive mutation:

   ```bash
   node scripts/sd-ai-command-pack-review-preflight.mjs \
     pre-archive --task-dir <exact-active-task-dir> [--task-dir ...] --json
   ```

   Require schema version 1, `status: valid`, and
   `pre_archive_valid`. A missing helper, malformed/unsupported result, nonzero
   exit, `invalid`, or `indeterminate` result stops before `task.py archive`,
   journal creation, staging, or commit. Report its stable reason codes and
   exact repo-relative paths; do not attempt a repair by mutating the task.
   Planning finalization intentionally skips this archive-only boundary; its
   deterministic mode selection is owned by the installed finalization
   evaluator, never inferred from a failed completion precheck.
   When no active task exists because the branch already contains a canonical
   archive/journal completion followed only by review-remediation commits, do
   not manufacture another task or session. Keep the captured base at the
   current exact head and continue to Step 7 in `completion` mode; the
   validator alone decides whether the bounded historical successor is valid.
5. Execute the skill with the current repository, branch, modified files, and
   session context. The Trellis skill is responsible for identifying the active
   task or session record and for keeping finalization idempotent; do not rerun
   it for the same state unless the user explicitly asks to recover from a
   failed prior run.
6. When the workflow reaches the journal-recording step, record the session
   with the pack wrapper instead of calling `add_session.py` directly:

   ```bash
   bash scripts/sd-ai-command-pack-toolchain.sh run-python -- \
     scripts/sd-ai-command-pack-record-session.py \
     --title "..." --summary "..." --commit "hash1,hash2" \
     --change "main change bullet" --change "..." \
     --test "- [OK] test result line" --test "..." \
     --no-commit
   ```

   The wrapper resolves each commit's subject from git (failing fast on
   unknown hashes), fills the Main Changes and Testing sections, and refuses
   to leave an entry that still contains template placeholders — the
   fill-and-amend dance after a bare `add_session.py` call is exactly what
   it removes. `--no-commit` prevents a nested Python process from writing the
   Git index in restricted agent environments. After the wrapper succeeds,
   inspect and commit only the journal it names and its sibling index:

   ```bash
   git status --short -- .trellis/workspace
   git add -- <exact-journal-path> <sibling-index.md>
   git diff --cached --check
   git commit -m "chore: record journal" -- \
     <exact-journal-path> <sibling-index.md>
   ```

   Do not stage the whole workspace or combine unrelated dirty files with this
   commit. If the wrapper script is missing, fall back to
   `add_session.py` and fill the `(Add details)`, `(Add test results)`, and
   `(see git log)` placeholders manually before pushing.
7. After the archive and journal commits exist, but before any push, create one
   private temporary receipt file and run the mode-specific final gate across
   the complete local bookkeeping range:

   ```bash
   FINISH_WORK_RECEIPT="$(mktemp)"
   node scripts/sd-ai-command-pack-review-preflight.mjs \
     final-bundle --mode <completion|planning> \
     --base <captured-finalization-base-oid> --head "$(git rev-parse HEAD)" \
     --json >"$FINISH_WORK_RECEIPT"
   ```

   Require schema version 1, `status: valid`, the expected mode-specific valid
   reason code, and an `evidence.headOid` equal to the current full HEAD OID.
   In `planning` mode, when the exact captured range contains only one newly
   completed journal session and its sibling index, the helper may
   automatically prove the session's already-published, single-parent,
   task-only work commits. A successful result remains
   `planning_bundle_valid` and identifies
   `evidence.planningSubtype: journal-only-recovery`; callers never select a
   third mode, widen the captured base, or reinterpret a failed result. This
   subtype verifies task-only scope and planning lifecycle state without
   retroactively applying current publication-quality content checks to work
   that predates the captured base. Normal task-plus-journal planning bundles
   retain their complete validation.
   In completion mode, a base equal to the current head may automatically
   recover one bounded adjacent archive/journal tail and prove every later
   first-parent commit as a `post-archive-review-successor`. The successor
   range may change code, tests, specs, and generated payloads, but never task,
   workspace, or finalization evidence. This remains ordinary `completion`
   mode and creates no duplicate journal or bookkeeping commit.

   Retain the private file path and exact JSON result as the finalization
   handoff for `sd-review-pr`, `sd-ship`, and `sd-housekeeping`. The
   housekeeping eligibility evaluator independently reruns the validator with
   the receipt's exact mode/base/head and requires the recomputed JSON to
   match. Only after validation may the existing flow perform its one final
   push. Preserve the file across a clean downstream review/ship/housekeeping
   handoff; delete it after housekeeping consumes it, the proof is abandoned,
   or the owning lifecycle blocks. If validation fails, preserve archive and journal commits locally
   for inspection: never amend, reset, drop, delete, or push them. Report the
   reason codes and the same command as the recovery recheck after the operator
   corrects the named artifacts.
8. Report what the skill completed, what remains for the user, and any
   validation or archival step that could not run.
