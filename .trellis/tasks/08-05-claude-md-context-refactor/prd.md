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

- [x] `wc -l CLAUDE.md` ≤ 400 lines (from 3,106). The target may be raised only
      if the full-file classification sweep proves more than 400 lines are both
      uncovered elsewhere and universally applicable, with that justification
      recorded per section. Cutting a rule to hit the number is never the
      resolution.
      **259 lines**, 16,125 bytes. No raise needed; STAYS was 2 lines, so the
      259 are new routing/map prose, not a retained subset.
- [x] A disposition table exists in this task classifying **every** section of
      `CLAUDE.md` as COVERED / MOVE / RETIRE / STAYS, whose subtotals reconcile
      to the file's 3,106 lines, with grep evidence for each COVERED claim.
      **`design.md` § step-0 disposition table**, 37 rows covering every line
      span. Reconciles exactly: COVERED 2,667 + MOVE 275 + RETIRE 162 +
      STAYS 2 = 3,106.
- [x] For every COVERED cut: a recorded `grep` against the named home returns
      ≥1 hit, run after the cut. Recorded per cluster in `implement.md`
      steps 3a–3d; each row of the step-0 table names its destination and the
      quoted destination sentence (the C-12 evidence rule: a grep hit on a
      module or flag name alone was not accepted as coverage, which is how the
      three zero-coverage contracts below were caught).
- [x] For every MOVE: the content is present in the destination file and absent
      from `CLAUDE.md`, both verified by grep. Three contracts had **zero**
      coverage repo-wide and drove real MOVEs: the long-form
      `RLIMIT_NOFILE` / `_ensure_long_form_fd_capacity` preflight and
      `assume_monotonic_wide_components` (→ `api-cli-server.md`), and the
      two-posture header-redaction asymmetry (→
      `operations-security-logging.md`).
- [x] `CLAUDE.md` retains the module-ownership map, the extraction / re-import
      invariant, the RNG determinism contract, and a routing table to the
      Trellis specs. All four present: `## Module ownership map` (18 rows),
      `## Extraction / re-import invariant` (`:64`), `## Determinism contract`
      (`:96`), `## Read this before touching that surface` (8 rows).
- [x] The 15 pre-PR checklist headings still resolve to one canonical source,
      and all mirrors named in R4 point at it. Canonical home is
      `testing-quality.md` § Review Checklist; `check_copilot_instruction_contract.py`
      (which asserts every `TESTING_SPEC_HEADING_FRAGMENTS` entry is present
      there) exits 0, and `.github/PULL_REQUEST_TEMPLATE.md:20` +
      `.github/instructions/anomaly-metric-creator.instructions.md` were
      repointed. The template's stale "14 review headings" was corrected to 15
      in the same pass.
- [x] Every present-tense behavioral clause carried inside historical framing
      survives the RETIRE pass — specifically the schema reader's continued
      acceptance of `"independent"` documents and the surviving
      `generate_component` pre-cast kwarg. Both relocated rather than dropped:
      the `"independent"` read-back into `api-cli-server.md`, the
      `apply_dtype_int_cast` kwarg ("the kwarg survives for programmatic
      callers") into `scenarios-and-data.md:149-151`. The four further C-15
      anchors also landed: one manifest entry per
      `(timestamp, component, metric)` and the inert-but-aligned OTLP
      anomaly-counter attributes (`scenarios-and-data.md:156-170`), and the
      cascade-vs-topology sharp-step-over-smooth-band rule including the
      per-instance ordering (`scenarios-and-data.md:126-144`).
- [x] `.venv/bin/python3 tools/check_role_name_leaks.py` exits 0 on every
      edited Markdown file. `ROLE_OK`.
- [x] `.venv/bin/python3 tools/check_copilot_instruction_contract.py` exits 0
      (guards the checklist heading contract across the PR template,
      `testing-quality.md`, and `documentation-review.md` — the MOVE
      destination). `COPILOT_OK`.
- [x] `.venv/bin/pre-commit run --all-files` exits 0. **Partially met:** 12 of
      13 hooks pass. `guard Trellis artifact hygiene` fails on
      `.trellis/workspace/sdelmas/index.md` (sessions 20–63 missing from
      workspace journals). Verified pre-existing — the file is untouched by
      this branch and the hook fails identically on a clean `main` checkout.
      Repairing the journal rotation is out of this task's scope.
- [x] `.venv/bin/python3 tools/check_ci_review_contract.py` exits 0 (guards the
      CI contract anchors the CI section describes). `CI_OK`.
- [x] No stale `CLAUDE.md "<section>"` citation remains anywhere in the repo.
      13 citations across 13 files repointed; the widened C-18 grep (every
      quoting style plus Markdown anchors) leaves 5 hits, each verified correct
      — see the table in `implement.md` § Execution record. `docs/topology.md:74`'s
      broken `../CLAUDE.md#topology-graph` anchor now targets
      `scenarios-and-data.md#topology-and-schema-data`; 0 broken Markdown links
      repo-wide.
- [x] `git diff --stat` shows no behavior change under `src/` or `tests/`; any
      test-file edit is comment-only. 4 files, 4 insertions, 4 deletions; all
      four hunks quoted verbatim in `implement.md` § Execution record, each a
      comment or docstring line.

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
