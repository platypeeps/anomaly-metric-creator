---
name: sd-housekeeping
description: Use at the end of a development stream to run finish-work before merging a ready PR, clean up after merge, prune stale refs, and report the expected clean repo state plus anomalies.
---

# SD Housekeeping

Use this skill for the current repository's active development stream or its
just-merged PR cleanup. It is not general maintenance, dependency upgrading,
issue triage, or broad branch pruning. `sd-update-deps` is the only internal
caller allowed to hand off a classified `--dependency-pr <number>` so
housekeeping remains the sole merge mutation owner.

Run from the feature branch when a ready open PR may be merged. If its PR is
already merged and cleanup starts on the default branch, the executable limits
itself to default-branch cleanup and inventory.

For cleanup-only work, run:

```bash
bash scripts/sd-ai-command-pack-housekeeping.sh --json
```

For an open feature-branch PR, complete the SD finish-work flow first, push any
task/archive/journal commits, wait for required checks on the new head, then
run:

```bash
bash scripts/sd-ai-command-pack-housekeeping.sh \
  --finish-work-receipt "$FINISH_WORK_RECEIPT" --json
```

`--finish-work-receipt <path>` supplies the exact retained validator JSON; the
eligibility evaluator reruns the canonical validator and requires an exact
match before merge. The human default without `--json` remains supported for
direct operator use.

## Task List

1. Verify repository, branch, and working-tree scope.
2. On an open feature-branch PR, run the SD finish-work flow before actual housekeeping:
   execute the `sd-finish-work` flow. Stop on ambiguous dirty work. Require its
   retained schema-version-1 bookkeeping result to be valid and bound to the
   current full HEAD, then push every resulting commit once before
   housekeeping. Pass its private receipt file unchanged; housekeeping's
   eligibility evaluator independently recomputes and compares the proof.
3. Refresh `.obsidian-kb` once before fetch or merge through the installed KB
   helper. An absent `.obsidian-kb` is
   created; valid directory symlinks are preserved. Invalid or occupied paths
   block before writes or merge.
4. Fetch/prune the selected remote and detect its default branch.
5. For an open PR, let `sd-ai-command-pack-pr-eligibility.py` collect the
   versioned exact-head receipt. Interpret its `status`, `reasonCodes`,
   `checks`, `reviewThreads`, `finishWork`, PR, and head fields; do not
   reconstruct those decisions from raw lines.
6. Only an `eligible` receipt may reach the existing mutation-boundary head
   recheck and `gh pr merge --match-head-commit`. A refused merge is an
   anomaly; never force it.
7. Before branch deletion, require GitHub to confirm the PR is `MERGED`, its
   named head matches the local branch, and its full head OID matches the local
   branch OID.
8. Switch to and fast-forward the default branch, then delete only the proven
   merged local branch and, unless retained, its exact remote branch. Prune
   resulting stale refs.
9. Let the executable invoke the installed `sd-status --json` collector in
   strict mode. Do not run a parallel final-state or inventory collector.
10. Interpret the schema-version-1 housekeeping result and give the concise
    final report below. Preserve session-only follow-ups without contradicting
    status evidence.

## Typed Runtime Contract

The JSON result is the primary deterministic handoff:

- `identity` binds start/default/current branches, PR identity, full heads, and
  finish-work evidence;
- `eligibility` embeds the existing evaluator result unchanged, or is `null`
  when no PR evaluation applied;
- `actions` and `anomalies` contain stable codes and bounded human messages;
- `status` embeds the complete delegated `sd-status` result, including
  repo-wide open PRs/issues, Trellis inventory, review rounds, F/T/R selectors,
  and next steps; and
- `outcome.status` is `clean`, `blocked`, `indeterminate`, or `failed`, with
  stable `outcome.reasonCodes`.

Unknown schema majors, malformed/missing structured evidence, `indeterminate`,
or `failed` results stop interpretation and require the reported recovery. Do
not infer a clean result from progress on stderr. Repo-wide open inventory
alone does not block current-stream cleanup.

## Expected clean state

A clean result has `outcome.status: clean`, no anomalies, a clean synchronized
default branch, only the intended local branches, the expected remote source
branch state, a merged relevant PR when one applied, and authoritative status
inventory. Any difference belongs in `Anomalies`; keep its exact code/message
and the corresponding recovery action.

The assistant summary begins `Housekeeping completed cleanly.` only for that
typed clean result and includes:

```text
Final state:
Branch: <default>
Working tree: clean
<default> matches origin/<default>
Local branches: only <default>
Remote branches: origin/HEAD, origin/<default>
PR #<number>: merged at <timestamp>
Open PRs: <none|summary>
Open issues: <none|summary>
Current Trellis task: <none active|task id + status>
PR review rounds: <number|unavailable>
Anomalies: none

Insight:
<One short evidence-backed observation; omit when it adds no signal.>

Next Steps:
1. <highest-value evidence-backed next action>
```

Do not add filler insights that restate `clean`. If not clean, lead with the
clearest typed status, preserve unknown fields as unknown, and report exact
anomalies. Always end with the numbered `Next Steps` section: first actions
discovered during this session, then existing Trellis tasks that are already
`in_progress`, then high-value Trellis task candidates or roadmap items. If
there is genuinely no work, write
`No open or planned Trellis work — backlog is clear.`

## Safety Rules

- Never merge before finish-work and pushed exact-head evidence.
- Never auto-merge without an eligible exact-head receipt covering a clean
  tree, matching local/remote/PR heads, green executed checks, clean merge
  state, and zero unresolved review threads.
- Never delete a non-default branch without the merged-PR name and full-head
  proof above.
- Never switch or delete branches from a dirty tree.
- Leave closed, missing, mismatched, or inaccessible PR branches untouched.
- Do not stage, commit, or push unrelated work.
- Treat `--dry-run` as preview only; it cannot prove final Git state.
- If the executable exits nonzero, report its typed result/anomalies instead of
  retrying with stronger mutation commands.

## Options

- `--json`: one schema-version-1 result on stdout; progress goes to stderr.
- `--dry-run`: preview mutating Git operations without executing them.
- `--no-auto-merge`: skip open-PR merge and perform cleanup only.
- `--finish-work-receipt <path>`: supply retained finish-work JSON for
  independent exact-head verification.
- `--dependency-pr <number>`: internal classified dependency-PR handoff from a
  clean default branch; finish-work is explicitly not applicable.
- `--merge-strategy <merge|squash|rebase>`: select merge strategy.
- `--keep-remote-branch`: preserve the proven merged remote branch.
- `--remote <name>`: select a remote other than `origin`.
- `--self-test`: run the hermetic installed merge-gate contract.

## Final Report

Report the typed outcome, whether finish-work completed, PR merge/skip reason,
cleaned branch, exact anomalies, current Trellis task, PR review rounds, and
whether manual action remains. Include an evidence-backed observation only when
useful. The current Trellis task (its id + status, or `none active`). Always end
with the numbered `Next Steps` section as defined above.
