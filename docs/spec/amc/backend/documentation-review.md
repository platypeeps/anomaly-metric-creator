# Documentation and Review

## Canonical Documentation Roles

Durable implementation and review conventions live in `docs/spec/`.
`AGENTS.md`, `CLAUDE.md`, GitHub/Copilot instructions, Claude/Codex/Gemini/
OpenCode files, and other platform entries are adapters or supporting source
documents. Sources: `AGENTS.md`; `CLAUDE.md`; `.trellis/workflow.md`;
`.github/instructions/anomaly-metric-creator.instructions.md`;
`.agents/`; `.codex/`; `.claude/`; `.gemini/`; `.opencode/`.

`README.md` and `docs/` own user-facing behavior: install, usage, CLI flags,
server endpoints, output files, topology prose, and application flow diagrams.
Keep them consistent with the implementation specs but do not turn them into a
duplicate agent rulebook. Sources: `README.md`;
`docs/application-flow.md`; `docs/topology.md`; `.trellis/tasks/`;
`docs/spec/amc/backend/`.

`CLAUDE.md` is the Claude Code adapter, sized to be loaded on every session:
the module-ownership map, the extraction / re-import invariant, the determinism
contract, the fixed generation-pipeline order, and a routing table into these
specs. Durable conventions go into Trellis first; `CLAUDE.md` links them rather
than restating them, and pre-refactor detail stays recoverable through
`git log`. Sources: `CLAUDE.md`; `AGENTS.md`;
`docs/spec/amc/backend/index.md`.

## Citation Rule

Every convention added to `docs/spec/` must cite supporting repo paths.
Prefer repo-relative paths; add line, symbol, or section detail when verified in
the current pass. Sources:
`.trellis/tasks/archive/2026-06/06-25-consolidate-agent-docs-trellis/prd.md`;
`.trellis/tasks/archive/2026-06/06-25-consolidate-agent-docs-trellis/design.md`;
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
`.trellis/tasks/archive/2026-08/08-05-claude-md-context-refactor/implement.md`.

When cutting prose from one surface on the grounds that another already covers
it, a grep hit on a module name, flag name, or keyword is **not** coverage.
Name the specific contract the prose asserts, then quote the destination
sentence that states that contract. Two "already covered" claims failed this
test during the `CLAUDE.md` consolidation, and three contracts turned out to
have zero coverage anywhere in the repo — they needed relocation, not deletion.
A consolidation pass should reconcile its dispositions against the source file's
line count so no span is silently unclassified. Sources:
`.trellis/tasks/archive/2026-08/08-05-claude-md-context-refactor/design.md`;
`.trellis/tasks/archive/2026-08/08-05-claude-md-context-refactor/implement.md`.

Retiring historical narrative is sentence-level work, not paragraph-level.
Passages written in past-tense project voice ("Phase N landed…", "PR #NN
widened…") routinely carry a live present-tense clause inside them — "the
reader still honors", "the kwarg survives", "no longer parses", "must not" —
and a paragraph delete drops a rule. Keep every clause asserting present
behavior; delete only the framing and superseded tuning history. Sources:
`.trellis/tasks/archive/2026-08/08-05-claude-md-context-refactor/design.md`;
`docs/spec/amc/backend/api-cli-server.md`;
`docs/spec/amc/backend/scenarios-and-data.md`.

Generated/local runtime state such as `.trellis/.runtime/`, session journals,
and task archives should not be the only source for product conventions. Use
them as historical context, then verify against code, tests, docs, or active
specs before codifying a rule. Sources: `.trellis/workflow.md`;
`.trellis/workspace/`; `.trellis/tasks/`; `src/anomaly_metric_creator/`;
`tests/`; `README.md`.

## Docs Sync

Behavior changes must update every surface that describes the behavior:
docstrings, CLI help strings, README, `docs/*.md`, Trellis specs, and adapter
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

Trellis task records are the canonical home for planned implementation work,
backlog slices, and follow-up decisions. User-facing docs can describe current
capabilities and supported behavior, but they should not carry a parallel list
of future work once the item has been converted into `.trellis/tasks/`.
Sources: `.trellis/tasks/archive/2026-08/07-09-multi-instance-dst-splice-boundary/prd.md`;
`.trellis/tasks/archive/2026-08/06-29-server-watch-semantics/prd.md`;
`.trellis/tasks/06-29-helm-incident-command-coverage/prd.md`; `README.md`.

When consolidating older planning or handoff notes, map each still-relevant
item to an active or archived Trellis task, create a new task only for a
current-doc item that has no tracker, then remove stale file references and
future-work phrasing from docs, journals, and task context manifests. Do not
leave the same work item tracked in both a user-facing document and a Trellis
task. Sources: `.trellis/tasks/archive/2026-08/07-09-multi-instance-dst-splice-boundary/prd.md`;
`.trellis/tasks/archive/2026-06/06-25-consolidate-agent-docs-trellis/`;
`.trellis/tasks/archive/2026-06/06-26-server-compat-debug-polish/`;
`.trellis/workspace/sdelmas/journal-1.md`.

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
`.trellis/tasks/08-06-conftest-helper-consolidation/prd.md`;
`.trellis/tasks/08-06-otlp-capture-fixture/prd.md`;
`.trellis/tasks/07-17-audit-test-harness-dedupe/prd.md`.

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

Sources: `.trellis/tasks/archive/2026-08/08-06-server-traces-mypy-gate/prd.md`;
`tests/test_task_criteria_lint.py`;
`.trellis/tasks/08-06-conftest-helper-consolidation/prd.md`;
`.trellis/tasks/08-06-otlp-capture-fixture/prd.md`;
`.trellis/tasks/07-17-audit-test-harness-dedupe/prd.md`.

## Repository Map Artifact

`docs/repomix-map.md` is the generated Repomix repository map for quick human
or LLM orientation. Development agents should use it when it is present before
doing broad repo-shape searches, then verify details against source files,
tests, docs, and Trellis specs before making changes. Sources:
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

The map excludes `.trellis/tasks/**`, so `task.py archive` never strands an
entry and the archive commit needs no map refresh. This is not tidiness: while
those paths were mapped, the archive commit had to carry a regenerated map to
pass the guard, and the command pack's completion finalization rejects
`docs/repomix-map.md` in the post-work delta with `bundle_scope_invalid`. Every
map-refreshing commit falls at or after the archive move and therefore inside
that delta, so the archive commit could satisfy the guard or the finalization
gate but never both. Restoring those paths to the map reintroduces a deadlock
that blocks *every* completion ship, not just an inconvenience — read the
comment in `scripts/update_repomix` first. One acknowledged cost: the command
pack's review preflight validates the `.trellis/` paths this map lists, and its
covered set shrinks to the non-task `.trellis/` trees.
Sources: `scripts/update_repomix`; `docs/DEVELOPMENT_CYCLE.md`;
`sd-ai-command-pack-review-preflight.mjs`.

## PR and Review Surfaces

The PR template checklist mirrors the required review headings, including the
changelog/version-impact gate for user-visible or compatibility changes. If a
heading is renamed, added, or removed in the Trellis review spec, update
`.github/PULL_REQUEST_TEMPLATE.md` and Copilot instructions in the same diff.
Sources: `docs/spec/amc/backend/testing-quality.md`;
`.github/PULL_REQUEST_TEMPLATE.md`;
`.github/instructions/anomaly-metric-creator.instructions.md`; `CLAUDE.md`.

Copilot instructions should route reviewers to the relevant Trellis spec first,
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
The pack's `sd-ai-command-pack-pr-body-scope.py` enforces these sections with
repo-specific categories from `.sd-ai-command-pack/pr-body-scope.json` when a
PR body is supplied through `SD_AI_COMMAND_PACK_PR_BODY_SCOPE_PR_BODY`,
`SD_AI_COMMAND_PACK_SCOPE_PR_BODY`, or `--body-file`. Sources:
`~/.agents/bin/sd-ai-command-pack-pr-body-scope.py`;
`.sd-ai-command-pack/pr-body-scope.json`; `tools/check_scope_heading_mirrors.py`;
`docs/DEVELOPMENT_CYCLE.md`.

The PR template should prompt for focused local checks, the local Trellis
full-check gate, and whether a remote `full-ci` label is needed. Review
guidance should prefer local evidence and the stable aggregate `test` context
before asking for repeated remote Copilot or Actions runs.
Sources: `.github/PULL_REQUEST_TEMPLATE.md`; `docs/DEVELOPMENT_CYCLE.md`;
`~/.agents/bin/sd-ai-command-pack-full-check.sh`; `tools/check_ci_review_contract.py`;
`tools/check_copilot_instruction_contract.py`;
`~/.agents/bin/sd-ai-command-pack-pr-body-scope.py`;
`~/.agents/bin/sd-ai-command-pack-review-preflight.mjs`;
`scripts/check-review-preflight.mjs`;
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
creating a pull request. Sources: `.trellis/workflow.md`;
`.trellis/workspace/`; `.trellis/tasks/`; `CLAUDE.md`.

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

Retain existing Codex, Claude, GitHub/Copilot, Gemini, and OpenCode Trellis
files as platform adapters. They should teach each platform how to load
Trellis context, not carry separate project conventions. Sources: `.agents/`;
`.codex/`; `.claude/`; `.github/`; `.gemini/`; `.opencode/`;
`.trellis/workflow.md`; `docs/spec/amc/backend/index.md`.

Generated Trellis platform files may be updated by future `trellis update`
runs. Keep local project conventions in `docs/spec/` or a project-local
skill rather than patching every generated copy with durable project rules.
Sources: `.trellis/workflow.md`; `.agents/skills/trellis-meta/`;
`.claude/skills/trellis-meta/`; `.github/skills/trellis-meta/`;
`.opencode/skills/trellis-meta/`.

Python hook adapters must not catch `BaseException` or use bare `except`, and
intentional fail-open `except Exception: pass` handlers must include a short
comment explaining the suppression. Enforce this mechanically instead of
copying reviewer prose into each generated adapter. Sources:
`.codex/hooks/`; `.github/copilot/hooks/`; `.gemini/hooks/`;
`tools/check_agent_hook_exceptions.py`;
`tests/test_agent_hook_exception_lint.py`.

Codex inline mode skips sub-agent JSONL curation and loads task artifacts/specs
through `trellis-before-dev`; sub-agent-capable platform files still keep their
context-loading protocols. Sources: `.trellis/config.yaml`;
`.trellis/workflow.md`; `.agents/skills/trellis-before-dev/SKILL.md`;
`.codex/agents/trellis-implement.toml`; `.claude/agents/trellis-implement.md`;
`.gemini/agents/trellis-implement.md`; `.opencode/agents/trellis-implement.md`.

## Historical Notes

Completed Trellis tasks and workspace journals are useful evidence, but they
can become stale. Before treating older planning text as active work, verify it
against current source, tests, README, and open tasks. Sources:
`.trellis/tasks/`; `.trellis/workspace/`; `src/anomaly_metric_creator/`;
`tests/`; `README.md`.
