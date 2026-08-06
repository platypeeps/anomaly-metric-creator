# Implement — CLAUDE.md context-cost refactor

Branch: create from `main` at `task.py start`. The uncommitted
`Multi-instance fan-out` merge already in the working tree is commit 1.

## Step 0 — Full-file classification sweep (replaces the earlier open questions)

The two planning-time open questions are resolved in `design.md` § Resolved
questions: `check_ci_review_contract.py` does not read `CLAUDE.md`, and the
Copilot instructions file carries checklist *headings only*. No step-0 research
remains on those.

What does remain is concern C-1: the disposition sample in `design.md` covers
only ~2,055 of 3,106 lines.

- [ ] Enumerate every `^## ` / `^### ` section in `CLAUDE.md` with its line
      count (`grep -n` + the sizing one-liner used during planning).
- [ ] Classify at **content-block granularity, not section granularity** (C-11).
      A single section routinely splits across dispositions — the CI section is
      110 COVERED + 49 MOVE, the server section 380 + 133 across two
      destinations. Assign each block exactly one disposition and record its
      line span.
- [ ] For every COVERED block, name **the specific contract** it asserts and
      quote the destination sentence that states it. A grep hit on a module
      name, flag name, or keyword is not coverage (C-12, C-13). Two claims
      already failed this test during review and are pre-classified MOVE:
      the `server_k8s_api_trace → server_ops_support._preview` DAG edge
      (absent from `architecture.md:211`) and the `full-ci` one-shot half of
      the application/CodeQL asymmetry (absent from `testing-quality.md:322`).
- [ ] Reconcile: the four disposition subtotals must sum to 3,106.
- [ ] If STAYS exceeds 400 lines, record why each surviving section is both
      uncovered elsewhere and universally applicable, and raise the target in
      `prd.md` with that justification. Do not cut a rule to hit a number.

Gate: the table sums to the file's line count, and no section is unclassified.

## Step 1 — Commit the in-flight merge

- [ ] `git add CLAUDE.md && git commit` — the two overlapping
      `### Multi-instance fan-out` sections merged into one (88 insertions,
      110 deletions).
- [ ] Validate: `grep -c "^### Multi-instance fan-out" CLAUDE.md` → `1`

## Step 2 — MOVE: relocate the unique content (must precede any cut)

### 2a — Pre-PR checklist bullets → `testing-quality.md`

- [ ] Move the 124-line checklist body under `testing-quality.md`
      § Review Checklist, keeping the 15 heading names and each heading's
      concrete bullets. Add the `Sources:` footer per that file's convention.
- [ ] Leave the 15 heading *names* in `CLAUDE.md` with a pointer; do not leave
      the bullets.
- [ ] Validate: `grep -c "table.get(key)" .trellis/spec/amc/backend/testing-quality.md` ≥ 1
      and `grep -c "table.get(key)" CLAUDE.md` → `0`
- [ ] Preserve every `TESTING_SPEC_HEADING_FRAGMENTS` entry in
      `testing-quality.md` — `tools/check_copilot_instruction_contract.py:336`
      asserts each one is present.
- [ ] Gate: `.venv/bin/python3 tools/check_copilot_instruction_contract.py`
      exits 0 (C-3).

### 2b — Copilot false-positives → `testing-quality.md`

- [ ] Move the five verified false-positive entries (cumulative-diff
      re-flagging, triplicated drift, `contents: read` cache claim, secrets in
      `if:`, preflight cell-cap) into the existing 3-line summary's section,
      expanding it. Add the `Sources:` footer per that file's convention (R3).
- [ ] Validate **one grep per moved entry**, not one per block (C-14). All five:
      cumulative-diff re-flagging, triplicated drift, `ACTIONS_RUNTIME_TOKEN`
      cache claim, secrets-in-`if:`, preflight cell-cap. Each must be present in
      `testing-quality.md` and absent from `CLAUDE.md`.

### 2c — CI uncovered detail → `testing-quality.md`

Zero-coverage items confirmed by grep: coverage threshold (`--fail-under=85`,
`COVERAGE_CORE=sysmon`, `relative_files`), the `!cancelled()` aggregate guard
and why `always()` is wrong, and the `ubuntu-latest-m` runner history.

Add the `full-ci` **one-shot** semantics for the application and Socket jobs
(C-13): `testing-quality.md:551` has the CodeQL-persistent half only, and
`CLAUDE.md` warns against unifying them because that cuts security coverage.

- [ ] Move those ~49 lines plus the one-shot half into `testing-quality.md`,
      with the `Sources:` footer per that file's convention (R3).
- [ ] Validate **every** moved contract, present in destination and absent from
      `CLAUDE.md` (C-14): `fail-under`, `COVERAGE_CORE`, `relative_files`,
      `cancelled()`, the `ubuntu-latest-m` runner rule, and the one-shot /
      persistent `full-ci` asymmetry.

Gate: `.venv/bin/python3 tools/check_ci_review_contract.py` exits 0. (No
`CLAUDE.md` anchor repointing is needed — that script does not read the file;
see `design.md` § Resolved questions.)

## Step 3 — COVERED cuts, driven by the step-0 table

Each cut: delete the prose, leave a one-to-three-line pointer, then run the
recorded grep against the destination. The four clusters below are the sample
from planning; step 0's table adds the remaining ~1,050 lines of sections, each
cut under the same evidence rule. Commit per cluster, or per spec destination
where that groups more naturally.

- [ ] 3a — Six lint sections (~380 lines) → pointer table naming each
      `tools/check_*.py`. Validate: for each of the five Python scripts,
      `python3 -c "import ast; print(len(ast.get_docstring(ast.parse(open(P).read())).splitlines()))"` ≥ 30.
      The sixth section is `tools/pr_comment.sh`, a shell wrapper with no Python
      docstring (C-18) — verify its header comment carries the contract, or the
      section is MOVE rather than COVERED. Note the circularity risk: three of
      these scripts currently cite `CLAUDE.md` as the policy home, so the
      pointer must be made bidirectional-safe by step 5's repoint.
- [ ] 3b — Server-mode module DAG / extraction ledger (~380 lines) → pointer
      to `architecture.md`. Validate: `grep -c server_ops_support .trellis/spec/amc/backend/architecture.md` ≥ 1
      for each of the seven leaf modules.
- [ ] 3c — Server-mode behavior contracts (~133 lines) → pointer to
      `operations-security-logging.md` + `api-cli-server.md`. Validate: grep
      each contract keyword (eval mode, watch, mutation) in those two files.
- [ ] 3d — CI covered remainder (~110 lines) → pointer to `testing-quality.md`.
      Validate: `heavy`, `loadfile`, `full-ci`, `Socket`, `check_mypy_gate`
      each ≥ 1 there.

## Step 4 — RETIRE the phase narrative (own commit, last)

Cut **sentence by sentence, never paragraph by paragraph** (C-2). Several
passages state current contract in phase-history voice; a paragraph delete drops
a live rule.

Keep-rule: any clause asserting present-tense behavior — "the reader still
honors", "the kwarg survives", "no longer parses", "stays exposed", "must not" —
is retained even when its surrounding framing is historical. Delete only "Phase
N landed / the phase-9 flag day removed / PR #NN widened" framing and superseded
tuning history.

- [ ] Delete per-phase narrative (~600 lines) across the topology,
      multi-instance, saturation, LLM-throttle, and validator sections.
- [ ] Record in this file the section list touched and the line count dropped.
- [ ] Validate the four known present-tense-in-historical-framing anchors
      survive:
      `grep -c "reader still honors" CLAUDE.md` ≥ 1 (schema v1 `independent`
      read-back, was `CLAUDE.md:971`);
      `grep -c "kwarg survives" CLAUDE.md` ≥ 1 (`generate_component` pre-cast
      contrast, was `:1342`);
      plus a read of the former `:1682` and `:1752` neighborhoods (topology mode,
      generation order) confirming their current-behavior clauses remain.
- [ ] Validate the four further anchors Codex found, none of which has any
      equivalent in the target specs (C-15): one manifest entry regardless of
      how many instances matched (was `:1164`); the per-instance cascade write
      still winning at its cell after topology composition (`:1305`); anomaly
      OTLP rows carrying no dimensions while logs/traces stay on the base
      attribute set (`:1592`); cascades remaining structurally present with
      sharp-step-over-smooth-band semantics (`:1851`).
- [ ] Validate: `grep -ciE "phase [0-9]" CLAUDE.md` drops to ≤ 5.
- [ ] Spot-read three edited sections end-to-end to confirm current behavior
      still reads self-contained without the removed narrative.

Rollback point: this commit is revertible alone.

## Step 5 — Rewrite the head into a routing table

- [ ] Replace the opening prose with: a one-paragraph statement of what the
      project is, the module-ownership table, the extraction / re-import
      invariant, the RNG determinism contract, and a "before touching X, read
      spec Y" routing table mirroring `.trellis/spec/amc/backend/index.md`.
- [ ] Repoint the two `.pre-commit-config.yaml` comments that cite `CLAUDE.md`
      section titles (C-6): line 1 (`CLAUDE.md "Pre-PR checklist > …"`) and
      line 125 (`CLAUDE.md "Workflow pip lint"`) — both sections are cut.
- [ ] Repoint three lint scripts' **runtime user-facing messages** (C-18), which
      tell operators where the policy lives and would otherwise dangle:
      `tools/check_branch_name.py:188`, `tools/check_approval_duplicate.py:623`,
      `tools/check_workflow_pip.py:217`. String-literal edits only (R5).
- [ ] Update the adapter files that assert a role `CLAUDE.md` no longer holds
      (C-17): `.github/PULL_REQUEST_TEMPLATE.md:20` ("CLAUDE.md remains expanded
      source detail") and `AGENTS.md:8` / `AGENTS.md:57` ("expanded
      historical/source guide … pre-PR checklist source material").
- [ ] Incidental fix while in the file (C-17): the PR template comment says
      "Mirrors the 14 review headings" but lists 15, and
      `instructions.md:219` says 15. Correct 14 → 15.
- [ ] Repoint `.github/instructions/anomaly-metric-creator.instructions.md`
      (C-16): item 3 at `:229` instructs reviewers to grep changed symbol names
      against `CLAUDE.md`, which will no longer hold them. Point it at the
      Trellis specs. Its per-heading normative body is a MOVE consideration —
      confirm each bullet's home before touching it.
- [ ] Fix the Markdown anchor `docs/topology.md:74`
      (`../CLAUDE.md#topology-graph`), which targets a removed section (C-19).
- [ ] Repoint or preserve the three test-file citations (C-7):
      `tests/test_topology_llm.py:21`, `tests/test_topology_saturation.py:393`,
      `tests/test_validate_output.py:866`. For each, either the cited statement
      stays in `CLAUDE.md` or the citation names the new home. Comment-only
      edits — no test logic changes (R5).
- [ ] Validate: `wc -l CLAUDE.md` ≤ 400, or the raised target justified per
      step 0.

## Step 6 — Full-scope check

- [ ] `wc -l CLAUDE.md` ≤ 400 (or the step-0 raised target, justified)
- [ ] `.venv/bin/python3 tools/check_role_name_leaks.py CLAUDE.md .trellis/spec/amc/backend/testing-quality.md .github/PULL_REQUEST_TEMPLATE.md .github/instructions/anomaly-metric-creator.instructions.md` → exit 0
- [ ] `.venv/bin/python3 tools/check_copilot_instruction_contract.py` → exit 0 (C-3)
- [ ] `.venv/bin/python3 tools/check_ci_review_contract.py` → exit 0
- [ ] `.venv/bin/pre-commit run --all-files` → exit 0
- [ ] No stale section citations. The narrow `CLAUDE.md "` grep misses three
      real cases (C-18), so match every quoting style and Markdown anchors:
      `grep -rnE "CLAUDE\.md[ ]*(#|\"|')|CLAUDE\.md under" . --include='*.py' --include='*.sh' --include='*.yaml' --include='*.yml' --include='*.md' --exclude-dir=.git`
      — every hit must name a section that still exists (C-6, C-7, C-18, C-19)
- [ ] Repo's own docs-consolidation gate (C-19), per
      `.trellis/spec/amc/backend/index.md:96` § Quality Check: run
      `get_context.py`, the Trellis placeholder scan, and the Markdown-link
      check
- [ ] `git diff --stat main` shows no behavior change under `src/` or `tests/`
      (test edits, if any, are comment-only per C-7)
- [ ] `git diff --check` → exit 0
- [ ] Step-0 disposition table complete, subtotals reconcile to 3,106, and every
      COVERED row carries its post-cut grep evidence
- [ ] Every `prd.md` acceptance criterion checked off with its evidence

Test suite is not required (documentation-only change, R5), but
`pre-commit run --all-files` covers the repo's Markdown and lint gates.

## Review gates

- After step 2: the MOVE destinations are correct and follow the
  `Sources:` convention — self-review before cutting anything.
- After step 4: read the three largest edited sections end-to-end. The RETIRE
  step is the one with no mechanical safety net.
- Before PR ready: walk the 15 pre-PR checklist headings, now living in
  `testing-quality.md`.

## Rollback

- Any single step: `git revert <sha>`.
- Everything: `git checkout main -- CLAUDE.md .trellis/spec/amc/backend/testing-quality.md`.
- Pre-refactor content is always recoverable via `git show main:CLAUDE.md`.
