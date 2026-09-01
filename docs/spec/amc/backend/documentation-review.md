# Documentation and Review

## Canonical Documentation Roles

Durable implementation and review conventions live in `docs/spec/`.
`AGENTS.md`, `CLAUDE.md`, GitHub/Copilot instructions, Claude/Codex/Gemini/
OpenCode files, and other platform entries are adapters or supporting source
documents. Sources: `AGENTS.md`; `CLAUDE.md`;
`.github/instructions/anomaly-metric-creator.instructions.md`;
`.agents/`; `.codex/`; `.claude/`; `.gemini/`; `.opencode/`.

`README.md` and `docs/` own user-facing behavior: install, usage, CLI flags,
server endpoints, output files, topology prose, and application flow diagrams.
Keep them consistent with the implementation specs but do not turn them into a
duplicate agent rulebook. Sources: `README.md`;
`docs/application-flow.md`; `docs/topology.md`; `docs/work/`;
`docs/spec/amc/backend/`.

`CLAUDE.md` is the Claude Code adapter, sized to be loaded on every session:
the module-ownership map, the extraction / re-import invariant, the determinism
contract, the fixed generation-pipeline order, and a routing table into these
specs. Durable conventions go into `docs/spec/` first; `CLAUDE.md` links them rather
than restating them, and pre-refactor detail stays recoverable through
`git log`. Sources: `CLAUDE.md`; `AGENTS.md`;
`docs/spec/amc/backend/index.md`.

## Citation Rule

Every convention added to `docs/spec/` must cite supporting repo paths.
Prefer repo-relative paths; add line, symbol, or section detail when verified in
the current pass. Sources:
`docs/work/archive/2026-06/2026-06-25-consolidate-agent-docs-trellis/prd.md`;
`docs/work/archive/2026-06/2026-06-25-consolidate-agent-docs-trellis/design.md`;
`AGENTS.md`.

A spec's `Sources:` footer must not cite `CLAUDE.md`. `CLAUDE.md` is derived
*from* this directory, so citing it as a source is circular — it makes a
restatement look like evidence. The only exception is a passage whose subject is
`CLAUDE.md`'s own role or its status as a lockstep mirror: this file's
`Canonical Documentation Roles`, `Docs Sync`, and `PR and Review Surfaces`
sections, plus `index.md`'s opening role statement and its
`Source Precedence` ranking of adapter docs. 94 citations were removed under
this rule — 89 across the six behavior specs and 5 from `index.md`'s
`Pre-Development Checklist`, whose routing list `CLAUDE.md` mirrors rather than
sources. None lost its evidence: every removed footer already cited the code,
test, or `README.md` path that substantiates the rule, confirmed by first
searching for footers where `CLAUDE.md` was the sole substantive source and
finding none. Sources: `docs/spec/amc/backend/index.md`;
`docs/work/archive/2026-08/2026-08-05-claude-md-context-refactor/implement.md`.

When cutting prose from one surface on the grounds that another already covers
it, a grep hit on a module name, flag name, or keyword is **not** coverage.
Name the specific contract the prose asserts, then quote the destination
sentence that states that contract. Two "already covered" claims failed this
test during the `CLAUDE.md` consolidation, and three contracts turned out to
have zero coverage anywhere in the repo — they needed relocation, not deletion.
A consolidation pass should reconcile its dispositions against the source file's
line count so no span is silently unclassified. Sources:
`docs/work/archive/2026-08/2026-08-05-claude-md-context-refactor/design.md`;
`docs/work/archive/2026-08/2026-08-05-claude-md-context-refactor/implement.md`.

Retiring historical narrative is sentence-level work, not paragraph-level.
Passages written in past-tense project voice ("Phase N landed…", "PR #NN
widened…") routinely carry a live present-tense clause inside them — "the
reader still honors", "the kwarg survives", "no longer parses", "must not" —
and a paragraph delete drops a rule. Keep every clause asserting present
behavior; delete only the framing and superseded tuning history. Sources:
`docs/work/archive/2026-08/2026-08-05-claude-md-context-refactor/design.md`;
`docs/spec/amc/backend/api-cli-server.md`;
`docs/spec/amc/backend/scenarios-and-data.md`.

Archived work items under `docs/work/archive/` should not be the only source
for product conventions. Use them as historical context, then verify against
code, tests, docs, or active specs before codifying a rule. Sources:
`docs/work/`; `src/anomaly_metric_creator/`; `tests/`; `README.md`.

## Docs Sync

Behavior changes must update every surface that describes the behavior:
docstrings, CLI help strings, README, `docs/*.md`, the specs under `docs/spec/`, and adapter
docs when those adapters mirror the changed convention. Sources: `CLAUDE.md`;
`README.md`; `docs/application-flow.md`; `docs/topology.md`;
`docs/spec/amc/backend/`; `.github/instructions/anomaly-metric-creator.instructions.md`.

When a default, precedence rule, count, edge list, dispatch order, artifact
name, flag, endpoint, or scenario changes, grep old and new wording across docs
and help text rather than relying on the touched file alone. Sources:
`CLAUDE.md`; `README.md`; `docs/application-flow.md`; `docs/topology.md`;
`src/anomaly_metric_creator/legacy.py`;
`src/anomaly_metric_creator/server.py`; `tests/`.

Magnitude and count prose must match executable data. Re-count scenario lists,
metric counts, flag counts, component sets, and workflow headings after adding
or removing entries. Sources: `CLAUDE.md`; `README.md`;
`src/anomaly_metric_creator/legacy.py`; `tests/test_scenarios.py`;
`tests/test_registry.py`; `.github/PULL_REQUEST_TEMPLATE.md`.

## Backlog and Follow-Up Ownership

Work items under `docs/work/` are the canonical home for planned implementation work,
backlog slices, and follow-up decisions. User-facing docs can describe current
capabilities and supported behavior, but they should not carry a parallel list
of future work once the item has been converted into `docs/work/`.
Sources: `docs/work/archive/2026-08/2026-07-09-multi-instance-dst-splice-boundary/prd.md`;
`docs/work/archive/2026-08/2026-06-29-server-watch-semantics/prd.md`;
`docs/work/archive/2026-09/2026-06-29-helm-incident-command-coverage/prd.md`; `README.md`.

When consolidating older planning or handoff notes, map each still-relevant
item to an active or archived work item, create a new item only for a
current-doc item that has no tracker, then remove stale file references and
future-work phrasing from the docs that carry them. Do not leave the same work
item tracked in both a user-facing document and a work item. Sources: `docs/work/archive/2026-08/2026-07-09-multi-instance-dst-splice-boundary/prd.md`;
`docs/work/archive/2026-06/2026-06-25-consolidate-agent-docs-trellis/`;
`docs/work/archive/2026-06/2026-06-26-server-compat-debug-polish/`.

An acceptance criterion that quotes a command must be runnable exactly as
written, state its expected output, and make a claim no wider than that command
checks. The recurring defect is a criterion whose prose asserts uniqueness --
"one derivation remains", "one capture harness remains" -- behind a command that
only constrains *location*: `grep -rn 'def helper' tests/` matching solely
`tests/conftest.py` still passes with the helper defined twice inside that file.
State a **count as well as a location**, and record what the command returns
*before* the change so the criterion's inversion is evidence-backed rather than
assumed. Two mechanical members of this family -- a multi-file `grep -c`, whose
per-file `file:count` lines can never be the single `0` a criterion claims, and
a GNU-only `\s`/`\d`/`\w` escape that stock BSD `grep` silently matches as a
literal -- are enforced by `tools/check_task_criteria_commands.py`; the
wider-claim defect is not mechanically detectable and remains this rule.
Sources: `tools/check_task_criteria_commands.py`;
`tests/test_task_criteria_lint.py`;
`docs/work/archive/2026-09/2026-08-06-conftest-helper-consolidation/prd.md`;
`docs/work/archive/2026-09/2026-08-06-otlp-capture-fixture/prd.md`;
`docs/work/archive/2026-09/2026-07-17-audit-test-harness-dedupe/prd.md`.

A pair of criteria written as exclusive `If X … / If not X …` branches always
leaves one box unchecked, and the pre-archive gate counts unchecked boxes — it
blocks on `pre_archive_acceptance_incomplete` without knowing the branch was
unreachable. Writing the design choice as two criteria is still right: it keeps
the decision legible instead of silently deleting the road not taken. Check
**both** boxes and mark the unreachable one explicitly, naming the branch that
was taken and why, rather than deleting it or leaving the gate to be argued
with:

Both criteria of the pair carry a tick, and the pair is introduced so a reader
knows why one of them cannot have been exercised:

```markdown
The last two criteria are the two exclusive branches of the shape decision.
The **rename** branch was taken, so the first is the one that had to be
satisfied and the second is recorded as not-taken rather than deleted.

- [x] If the method was renamed, no caller uses the old name:
      `grep -rn '\.list(' src/ tests/` returns 0 matches.
- [x] If the method name was kept, the mechanical lint exists, has tests, and
      fails on a bare `list[...]` annotation added inside `CommandTraceStore`.
      → **branch not taken.** The rename branch was taken instead
      (`CommandTraceStore.list` → `list_traces`), so no lint is required.
```

Sources: `docs/work/archive/2026-08/2026-08-06-server-traces-mypy-gate/prd.md`;
`tests/test_task_criteria_lint.py`;
`docs/work/archive/2026-09/2026-08-06-conftest-helper-consolidation/prd.md`;
`docs/work/archive/2026-09/2026-08-06-otlp-capture-fixture/prd.md`;
`docs/work/archive/2026-09/2026-07-17-audit-test-harness-dedupe/prd.md`.

## Repository Map Artifact

`docs/repomix-map.md` is the generated Repomix repository map for quick human
or LLM orientation. Development agents should use it when it is present before
doing broad repo-shape searches, then verify details against source files,
tests, docs, and the specs under `docs/spec/` before making changes. Sources:
`docs/repomix-map.md`; `AGENTS.md`; `docs/spec/amc/backend/index.md`;
`docs/spec/guides/cross-layer-thinking-guide.md`.

Refresh the map with `scripts/update_repomix` whenever code, docs, tests,
scripts, or platform-adapter tree changes make the artifact stale. The script
is the canonical refresh command, writes `docs/repomix-map.md` in place, and
passes `--no-git-sort-by-changes` so identical repository contents retain
stable ordering instead of producing change-recency churn.
Sources: `scripts/update_repomix`; `README.md`; `docs/repomix-map.md`.

`tools/check_repomix_map_freshness.py` enforces **one** of the two staleness
directions: every path the map lists must still resolve to a tracked file or
directory. The reverse — a tracked file that never appears in the map — is
deliberately unguarded, because detecting it requires reproducing repomix's
built-in default ignore set, which lives in the tool and in no file here. Do not
close that gap with a hand-maintained mirror of the upstream list: it would be a
second registry for the same fact, drifting on every repomix upgrade with no
guard of its own. Refreshing after a tree change is therefore still a discipline,
not something the guard can enforce for you.

The guard is `always_run` in pre-commit and takes no path operands, because
staleness comes from files moving *elsewhere* while the map stays unchanged — a
`files:`-selected hook would run only on the commits that cannot be stale. It
resolves against the git index rather than the filesystem, so untracked local
debris cannot mask a stale entry that would fail in CI.
Sources: `tools/check_repomix_map_freshness.py`; `.pre-commit-config.yaml`;
`docs/DEVELOPMENT_CYCLE.md`; `CLAUDE.md`.

The only path `scripts/update_repomix` excludes is the artifact itself
(Repomix's built-in defaults also drop `uv.lock`), so a commit that archives a
work item strands entries unless it carries a regenerated map. Archive and
refresh in the same commit.

That was reversed until 2026-08-30, and the history is worth keeping because
the exclusion looked like tidiness and was not. The work-item tree was excluded
because the command pack's completion finalization rejected
`docs/repomix-map.md` in the post-work delta with `bundle_scope_invalid`, while
every map-refreshing commit falls at or after the archive move and therefore
inside that delta: the archive commit could satisfy the freshness guard or the
finalization gate, never both. That gate left with the pack, the deadlock went
with it, and the exclusion was removed rather than kept as a habit. Read the
comment in `scripts/update_repomix` before changing this again.
Sources: `scripts/update_repomix`; `docs/DEVELOPMENT_CYCLE.md`;
`tools/check_repomix_map_freshness.py`.

## PR and Review Surfaces

The PR template checklist mirrors the required review headings, including the
changelog/version-impact gate for user-visible or compatibility changes. If a
heading is renamed, added, or removed in the review spec, update
`.github/PULL_REQUEST_TEMPLATE.md` and Copilot instructions in the same diff.
Sources: `docs/spec/amc/backend/testing-quality.md`;
`.github/PULL_REQUEST_TEMPLATE.md`;
`.github/instructions/anomaly-metric-creator.instructions.md`; `CLAUDE.md`.

Copilot instructions should route reviewers to the relevant spec first,
then to source files/tests and supporting historical sections as needed. They
should not redefine project rules independently. Sources:
`.github/instructions/anomaly-metric-creator.instructions.md`;
`docs/spec/amc/backend/index.md`;
`tools/check_copilot_instruction_contract.py`; `CLAUDE.md`; `README.md`.

PR descriptions must name behavior changes, list the test plan, and walk the
review checklist before draft status is removed. Sources: `CLAUDE.md`;
`.github/PULL_REQUEST_TEMPLATE.md`;
`docs/spec/amc/backend/testing-quality.md`.

Behavior-changing diffs should use explicit scope sections in the PR body:
`Automation scope:`, `CI/review scope:`, `Tooling/generated scope:`,
`Docs/user-facing scope:`, or `Runtime/server scope:` as applicable.
Nothing enforces them. The check that did was pack-owned and left with the
pack on 2026-08-30, so a missing scope section is caught in review, not by a
gate. Sources: `.github/PULL_REQUEST_TEMPLATE.md`; `docs/DEVELOPMENT_CYCLE.md`.

The PR template should prompt for focused local checks, the local
deterministic gate (`pre-commit run --all-files` plus
`scripts/check-review-preflight.mjs`), and whether a remote `full-ci` label is
needed. Review guidance should prefer local evidence and the stable aggregate
`test` context before asking for repeated remote Copilot or Actions runs.
Sources: `.github/PULL_REQUEST_TEMPLATE.md`; `docs/DEVELOPMENT_CYCLE.md`;
`tools/check_ci_review_contract.py`;
`tools/check_copilot_instruction_contract.py`;
`scripts/check-review-preflight.mjs`; `.pre-commit-config.yaml`;
`.github/copilot-instructions.md`;
`.github/instructions/anomaly-metric-creator.instructions.md`.

Recurring review lessons that are specific to AMC belong in
`docs/REVIEW_PATTERNS.md` or a mechanical `tools/check_*.py` guard with tests,
not only in PR comments. Sources: `docs/REVIEW_PATTERNS.md`;
`docs/spec/amc/backend/testing-quality.md`; `tools/`;
`tests/test_*_lint.py`.

Before opening housekeeping or finish-work PRs, fetch and compare against
`origin/main` so already-merged archive/journal commits do not become redundant
PRs. A publish flow should have a non-empty, non-duplicate branch diff before
creating a pull request. Sources: `docs/DEVELOPMENT_CYCLE.md`;
`docs/work/`; `CLAUDE.md`.

Externally posted comment bodies (`gh pr comment`, `gh issue comment`,
`gh pr create --body-file`, `gh pr review --body-file`) must pass two body
gates before posting: the role-name-leak gate (`tools/check_role_name_leaks.py`,
stdin `-` mode) and the approval-duplicate gate
(`tools/check_approval_duplicate.py`, `--pr N` mode). `tools/pr_comment.sh` is
the canonical wrapper that runs both gates and then posts, so the conventions
have a live enforcement path rather than only prose; it redirects the body file
into each gate independently (each gate reads the full body from stdin, so a
single pipe would misfeed the second gate) and passes the 0/1/2 gate contract
through. It is operator tooling, not a CI step. Sources: `tools/pr_comment.sh`;
`tools/check_role_name_leaks.py`; `tools/check_approval_duplicate.py`;
`tests/test_role_name_leaks_lint.py`; `tests/test_approval_duplicate_lint.py`;
`CLAUDE.md`.

## Platform Adapter Policy

The repository's skills live under `.agents/skills/`. That tree is the source;
`.claude/`, `.codex/`, `.gemini/`, `.github/`, and `.opencode/` each carry a
rendered copy of it, produced by `scripts/sync-agent-skills.py`, which
enumerates the source directory rather than working from a roster. Edit the
`.agents/` copy and re-run the sync; never hand-edit a rendered copy.

Nothing compares the six copies mechanically -- `sync-agent-skills.py --check`
reports drift when it is run, but no hook or CI step runs it -- so a hand-edited
render is caught in review, not by a gate.

Skills should teach a platform how to load the specs under `docs/spec/`, not
carry separate project conventions. Sources: `.agents/skills/`;
`scripts/sync-agent-skills.py`; `docs/spec/amc/backend/index.md`.

## Historical Notes

Archived work items are useful evidence, but they can become stale. Before
treating older planning text as active work, verify it against current source,
tests, README, and the open items under `docs/work/`. Sources:
`docs/work/`; `src/anomaly_metric_creator/`;
`tests/`; `README.md`.
