# CLAUDE.md context-cost refactor

## Goal

Cut `CLAUDE.md` from 3,106 lines (195 KB, ~77.9k tokens) to a lean project
memory file, without losing any durable rule. Every line removed must either
already exist in another live source, or be relocated to the canonical source
`CLAUDE.md` itself declares.

`CLAUDE.md` is loaded in full on every session start and every compaction. At
77.9k tokens it consumes 26% of the 300k window before any work begins. It is
also self-contradicting: its own first paragraph states that "canonical
development conventions now live in `.trellis/spec/amc/backend/index.md`", then
restates roughly 3,000 lines of them.

## Requirements

### R1 — No rule is lost

Every cluster removed from `CLAUDE.md` must be shown to survive in a live
source before the cut lands. Three dispositions are allowed, and each cut must
be labelled with one:

- **COVERED** — the content already exists in a Trellis spec, a `docs/` file, a
  script module docstring, `README.md`, `CHANGELOG.md`, or the source file
  itself. Cut and replace with a pointer.
- **MOVE** — the content is unique to `CLAUDE.md`. Relocate it into the
  canonical home before cutting.
- **RETIRE** — the content is historical narrative with no forward-looking
  rule (e.g. "Phase 4 landed X, phase 9 removed it"). Drop deliberately, with
  the decision recorded in this task.

### R2 — CLAUDE.md keeps only universal, always-needed context

What stays: the module map (which file owns which surface), the extraction /
re-import invariant, the RNG determinism contract, the "read this spec before
touching that surface" routing table, and pointers. What goes: per-phase
narrative, per-lint prose, per-module extraction ledgers, and any checklist
whose canonical home is elsewhere.

### R3 — Canonical-source contract is honored, not re-broken

Content moved into `.trellis/spec/amc/backend/*.md` must follow that
directory's existing conventions, including the `Sources:` path-citation
footers each section carries.

### R4 — Downstream mirrors stay in lockstep

`CLAUDE.md` mandates lockstep between the pre-PR checklist and
`.github/PULL_REQUEST_TEMPLATE.md`,
`.github/instructions/anomaly-metric-creator.instructions.md`,
`tools/check_ci_review_contract.py`, and the Trellis specs. Moving the
checklist must not break that contract; if the canonical home changes, every
mirror's pointer updates in the same change.

### R5 — No source-code behavior change

This task edits documentation only. No file under `src/`, `tests/`, `tools/`,
or `.github/workflows/` changes behavior. Edits to `tools/check_*.py` are
limited to docstrings if a pointer needs to name them.

## Constraints

- `CLAUDE.md` is read by Claude Code, and `AGENTS.md` / `.github/instructions/`
  serve the same role for other agents. Do not create a fourth parallel copy.
- The repo's own doc-drift rule applies to this change: grep every changed
  symbol name against `README.md`, `docs/`, and the Trellis specs.
- `tools/check_role_name_leaks.py` must pass on every edited Markdown file.
- Work in one PR unless the diff exceeds reviewable size; if split, the
  checklist relocation (R4) is its own commit because it touches four mirrors.

## Out of scope

- Rewriting the Trellis specs for their own sake.
- Changing MCP server configuration or skill files (separate audit findings).
- `README.md` restructuring (97.8 KB, user-facing, loaded on demand — not a
  context cost).

## Acceptance Criteria

- [ ] `wc -l CLAUDE.md` ≤ 400 lines (from 3,106). The target may be raised only
      if the full-file classification sweep proves more than 400 lines are both
      uncovered elsewhere and universally applicable, with that justification
      recorded per section. Cutting a rule to hit the number is never the
      resolution.
- [ ] A disposition table exists in this task classifying **every** section of
      `CLAUDE.md` as COVERED / MOVE / RETIRE / STAYS, whose subtotals reconcile
      to the file's 3,106 lines, with grep evidence for each COVERED claim.
- [ ] For every COVERED cut: a recorded `grep` against the named home returns
      ≥1 hit, run after the cut.
- [ ] For every MOVE: the content is present in the destination file and absent
      from `CLAUDE.md`, both verified by grep.
- [ ] `CLAUDE.md` retains the module-ownership map, the extraction / re-import
      invariant, the RNG determinism contract, and a routing table to the
      Trellis specs.
- [ ] The 15 pre-PR checklist headings still resolve to one canonical source,
      and all mirrors named in R4 point at it.
- [ ] Every present-tense behavioral clause carried inside historical framing
      survives the RETIRE pass — specifically the schema reader's continued
      acceptance of `"independent"` documents and the surviving
      `generate_component` pre-cast kwarg.
- [ ] `.venv/bin/python3 tools/check_role_name_leaks.py` exits 0 on every
      edited Markdown file.
- [ ] `.venv/bin/python3 tools/check_copilot_instruction_contract.py` exits 0
      (guards the checklist heading contract across the PR template,
      `testing-quality.md`, and `documentation-review.md` — the MOVE
      destination).
- [ ] `.venv/bin/pre-commit run --all-files` exits 0.
- [ ] `.venv/bin/python3 tools/check_ci_review_contract.py` exits 0 (guards the
      CI contract anchors the CI section describes).
- [ ] No stale `CLAUDE.md "<section>"` citation remains anywhere in the repo.
- [ ] `git diff --stat` shows no behavior change under `src/` or `tests/`; any
      test-file edit is comment-only.

## Notes

- Prior work in this session already merged the two overlapping
  `### Multi-instance fan-out` sections (net −22 lines, 88 insertions /
  110 deletions). That change is uncommitted in the working tree at task
  creation time and belongs to this task's branch.
- Measured section sizes (post-merge) that drive the plan:
  `Server mode and ops command simulation` 513, `Topology graph` 190,
  `Per-instance topology` 161, `Continuous integration and Dependabot`
  159, `Multi-instance fan-out` 144, `Scenario registry` 140,
  `Pre-PR checklist` 124, plus six lint sections totalling ~380.
