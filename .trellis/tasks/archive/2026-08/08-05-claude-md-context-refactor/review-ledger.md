# Planning adversarial review — concern ledger

Trigger: `prd.md` rewritten from template, `design.md` and `implement.md` newly
created in this batch. All three are new or materially changed, so the review
applies.

Round 1. Host lane: complete. Codex lane: launched (task `brel8t409`,
`codex exec --cd . --sandbox read-only --ephemeral`), still running at
collection time — see status note at the bottom.

## C-1 — design.md disposition analysis covers only two thirds of the file · BLOCKING

**Evidence.** `design.md` § Disposition analysis sums to ~1,003 COVERED + ~202
MOVE + ~600 RETIRE + ~250 stays = ~2,055 lines. `wc -l CLAUDE.md` is 3,106.
About 1,050 lines are unclassified, including entire sections measured during
planning: `Topology graph` 190, `Per-instance topology` 161, `Multi-instance
fan-out` 144, `Scenario registry` 140, `LLM token-throttle` 108, `Output
validator` 103, `Output directory hygiene` 96, `Gauge metric file` 84,
`schema.json` 81, `Saturation feedback` 80, `Combine step` 77, `Parallel
execution` 74, `CLI surface` 66, `Adding new components` 60.

Consequence: the `≤400 lines` acceptance criterion is unreachable from the
stated plan. 3,106 − 2,055 leaves 1,301 lines even if every planned cut lands.

**Disposition: addressed.** `design.md` gains a full-file classification
requirement, and `implement.md` step 3 becomes a sweep over *every* section
rather than four named clusters. The `≤400` criterion in `prd.md` gains an
evidence-based escape: if the sweep proves more than 400 lines are both
uncovered elsewhere and universally applicable, the target is raised with the
justification recorded, rather than forcing a cut that loses rules.

## C-2 — RETIRE would drop still-load-bearing rules wearing historical framing · BLOCKING

**Evidence.** Four passages state current, forward-looking behavior in
phase-history voice:

- `CLAUDE.md:971` — the writer only emits `"realistic"` since the phase-9 flag
  day, "the reader still honors `"independent"` so documents produced under the
  historic mode keep validating". The reader behavior is current contract.
- `CLAUDE.md:1342` — the flag was removed, "the `generate_component` kwarg
  survives for programmatic callers that need the pre-cast fractional
  contrast". Current API surface.
- `CLAUDE.md:1682`, `CLAUDE.md:1752` — same shape for topology mode and
  generation order.

A paragraph-level "delete phase narrative" pass drops these.

**Disposition: addressed.** `implement.md` step 4 becomes sentence-level, with
an explicit keep-rule: any clause asserting present-tense behavior ("the reader
still honors", "the kwarg survives", "no longer parses", "stays exposed") is
retained even when its surrounding framing is historical. Added validation:
grep the four cited line anchors' key phrases after the cut and confirm each
still resolves.

## C-3 — implement.md omits the hook that guards the checklist contract · non-blocking

**Evidence.** `.pre-commit-config.yaml:160` registers hook
`copilot-instruction-contract` → `tools/check_copilot_instruction_contract.py`,
whose file set includes `.trellis/spec/amc/backend/testing-quality.md`
(`tools/check_copilot_instruction_contract.py:28`). Lines 336–344 assert that
every `TESTING_SPEC_HEADING_FRAGMENTS` entry appears in that file. Step 2a
edits exactly that file; `implement.md` step 6 does not run this hook.

**Disposition: addressed.** Added to `implement.md` step 2a gate and step 6.

## C-4 — design.md open question 1 · resolved, rebutted

**Evidence.** `grep -n "CLAUDE" tools/check_ci_review_contract.py` returns no
matches. The script does not read `CLAUDE.md`, so no anchor repointing is
needed for the CI MOVE.

**Disposition: rebutted.** Open question closed in `design.md`; the
`check_ci_review_contract.py` run stays in step 6 as a regression guard only.

## C-5 — design.md open question 2 · resolved, rebutted

**Evidence.** `.github/instructions/anomaly-metric-creator.instructions.md:217`
is `## Pre-PR checklist headings (canonical in Trellis)` — headings only, and it
already declares Trellis canonical. `check_copilot_instruction_contract.py`
verifies the headings match `PR_CHECKLIST_HEADINGS` exactly.

**Disposition: rebutted.** Not a second MOVE source. Open question closed.

## C-6 — pre-commit comments cite CLAUDE.md section titles · non-blocking

**Evidence.** `.pre-commit-config.yaml:1` cites `CLAUDE.md "Pre-PR checklist >
…"`; `:125` cites `CLAUDE.md "Workflow pip lint"`. Both sections are cut or
renamed by this plan, leaving stale pointers — the exact doc-drift pattern
`CLAUDE.md` itself names as the most-flagged review issue.

**Disposition: addressed.** `implement.md` step 5 gains a repoint of both
comments, and step 6 gains a grep for `CLAUDE.md "` citations across the repo.

## C-7 — three test files cite CLAUDE.md prose · non-blocking

**Evidence.** `tests/test_topology_llm.py:21` ("Decision (documented in
CLAUDE.md): no synthetic `token_limiter`"), `tests/test_topology_saturation.py:393`,
`tests/test_validate_output.py:866` ("CLAUDE.md promises …"). Each asserts that
a specific statement lives in `CLAUDE.md`. Cutting the cited statements makes
the citations false.

**Disposition: addressed.** Step 3/4 must, for each of the three citations,
either keep the cited statement in `CLAUDE.md` or update the citation to the new
home. Added as a step-6 check.

## C-8 — role-name lint scans CLAUDE.md · rebutted

**Evidence.** `tests/test_role_name_leaks_lint.py:339` includes `CLAUDE.md` in a
live repo scan. Shrinking the file only reduces the scanned input; the lint has
no content expectation.

**Disposition: rebutted.** No action.

## Cross-artifact figure consistency

Checked each measured value for every occurrence across the task directory:

- `3,106` / `195 KB` / `77.9k tokens` / `26%` — `prd.md` goal and `design.md`
  tier table; consistent.
- `2,196` spec lines / `13` files — `design.md` only; matches measured
  `wc -l .trellis/spec/amc/backend/*.md` (Σ 2196, 13 files).
- Section sizes (513 / 190 / 161 / 159 / 144 / 140 / 124) — appear in `prd.md`
  Notes and `design.md` Disposition analysis; consistent.
- `88 insertions / 110 deletions`, net −22 — `prd.md` Notes and `implement.md`
  step 1; consistent with `git diff --stat`.
- Lint docstring line counts (71 / 82 / 53 / 44 / 38) — `design.md` and
  `implement.md` step 3a; consistent with the measured AST values.
- `task.json` carries no measurements.

One inconsistency found and folded into C-1: `design.md`'s disposition subtotal
did not reconcile with the file's own line count.

## C-9 — MOVE validations checked destination presence but not source absence · non-blocking

**Evidence.** `prd.md` acceptance requires, for every MOVE, that the content is
"present in the destination file and absent from `CLAUDE.md`, both verified by
grep". Round-1 `implement.md` steps 2b and 2c validated only destination
presence, so a copy-instead-of-move would pass the plan's own gate.

**Disposition: addressed.** Both steps gained the absence grep against
`CLAUDE.md`. (2a already had it.)

## C-10 — `Sources:` convention required by R3 was only wired into step 2a · non-blocking

**Evidence.** `prd.md` R3 requires moved content to carry the
`.trellis/spec/amc/backend/` `Sources:` path-citation footer. Round-1
`implement.md` named it in step 2a only; 2b and 2c omitted it.

**Disposition: addressed.** Added to steps 2b and 2c.

Round-2 host review also re-checked figure consistency after the edits: `3,106`
now appears in `prd.md` (goal, acceptance), `design.md` (subtotal reconciliation),
and `implement.md` (step 0 gate); all agree. The false-positive count in step 2b
was corrected from "four" to "five" to match the enumerated list.

## Round 1 result

Blocking: C-1, C-2 — both addressed by artifact changes below. Non-blocking:
C-3, C-6, C-7 — addressed. Rebutted: C-4, C-5, C-8.

Because addressed concerns changed the artifacts, contract §4 requires a
round-2 host review against the updated set, plus one fresh Codex review if
Codex was available in round 1.

## Round 2 — Codex lane (completed)

The round-1 Codex launch hung: `codex exec` was given no stdin redirect, so it
blocked reading stdin — 0.08s CPU over 50 minutes, state `S`. Killed and
re-run with `</dev/null`; a trivial probe returned in under two minutes,
confirming auth and flags were never the problem. The real review then
completed (exit 0, 149,824 tokens) against the **round-1-remediated** artifact
set, which is what contract §4 asks for.

Nine concerns. Every one was spot-verified against the repository before
disposition; all nine held.

### C-11 — one-disposition-per-section cannot reconcile mixed-content sections · BLOCKING

**Evidence.** `implement.md` step 0 assigned one disposition per section, but
`design.md`'s own table already splits single sections: the CI section is 110
COVERED + 49 MOVE, and the server section is 380 + 133 across two destinations.
Line reconciliation to 3,106 is impossible at section granularity.

**Disposition: addressed.** The classification unit becomes the *content block*,
not the section. A section may contribute lines to several dispositions;
reconciliation sums blocks.

### C-12 — the server-DAG COVERED claim is false · BLOCKING

**Evidence.** `CLAUDE.md:374` records `server_k8s_api_trace` importing from
`server_k8s_api` **and** `server_ops_support._preview`.
`architecture.md:211` records only "importing one-way from `server_k8s_api`".
The `_preview` edge is absent. The planned module-name grep passes anyway,
because the module name appears — coverage was measured by token presence, not
by contract.

**Disposition: addressed.** COVERED now requires naming the specific contract
and confirming the destination *states* it. This edge is re-dispositioned
COVERED→MOVE.

### C-13 — the `full-ci` COVERED claim preserves only half the contract · BLOCKING

**Evidence.** The load-bearing content is the *asymmetry*: application and
Socket jobs honor `full-ci` one-shot, while CodeQL re-checks it persistently,
and `CLAUDE.md` explicitly warns "Do not unify the two by making CodeQL
one-shot (that cuts security coverage)". `testing-quality.md:551` carries the
CodeQL-persistence half only; its application-lane prose (`:322`) says merely
that the label runs the full lane.

**Disposition: addressed.** Re-dispositioned COVERED→MOVE for the one-shot half.

### C-14 — MOVE verification samples one token per block · BLOCKING

**Evidence.** Five Copilot false-positive entries are gated by a single
`ACTIONS_RUNTIME_TOKEN` grep; the CI move by `fail-under` + `cancelled()` only,
omitting `COVERAGE_CORE`, `relative_files`, and the runner rule. Four of five
entries could vanish with every gate green.

**Disposition: addressed.** One grep per moved *contract*, enumerated.

### C-15 — RETIRE anchor list is incomplete · BLOCKING

**Evidence.** Four further present-tense contracts with no equivalent in the
target specs (Codex ran the confirming greps against
`.trellis/spec/amc/backend/`, all empty): one manifest entry regardless of
matched instance count (`CLAUDE.md:1164`); per-instance cascade writes winning
after topology composition (`:1305`); anomaly OTLP rows remaining dimensionless
with logs/traces on base attributes (`:1592`); cascades remaining structurally
present with sharp-step semantics (`:1851`).

**Disposition: addressed.** Anchors added to the step-4 keep-list.

### C-16 — C-5 was rebutted incorrectly · BLOCKING

**Evidence.** `.github/instructions/anomaly-metric-creator.instructions.md:219`
carries a numbered body of normative guidance under every heading, not headings
only ("non-canonical inputs enumerated; every discriminator branch validated;
dispatch tables strict"). Item 3 at `:229` further instructs reviewers to grep
changed symbol names **against `CLAUDE.md`** — an instruction the refactor
falsifies. `check_copilot_instruction_contract.py:307` compares only extracted
heading names and loose fragments, so passing it proves nothing about the body.

**Disposition: C-5 reversed — was `rebutted`, now `addressed`.** The
instructions file is both a second MOVE consideration and a required repoint.
The round-1 rebuttal read the heading and the contract checker, not the body.

### C-17 — adapter files assert things the refactor makes false · BLOCKING

**Evidence.** `.github/PULL_REQUEST_TEMPLATE.md:20` states it "Mirrors the 14
review headings" and that "CLAUDE.md remains expanded source detail".
`AGENTS.md:8` and `:57` likewise describe `CLAUDE.md` as the "expanded
historical/source guide … pre-PR checklist source material". Step 5 updated only
two pre-commit comments and three test citations.

**Separately: the "14" is already wrong.** The template lists 15 `- [ ]`
headings and `instructions.md:219` says "15-heading checklist". That is a
pre-existing drift this task did not create but should fix while in the file.

**Disposition: addressed.** Both adapters added to step 5; the 14→15 correction
recorded as an in-scope incidental fix.

### C-18 — lint scripts point *back* at the sections being cut · BLOCKING

**Evidence.** These are runtime user-facing messages, not just docstrings:
`tools/check_branch_name.py:188` ("Policy lives in CLAUDE.md under
'Branch-name lint'"), `tools/check_approval_duplicate.py:623` ("… under
'Approval-duplicate lint'"), `tools/check_workflow_pip.py:217` ("see CLAUDE.md
'Pre-PR checklist > CI / workflow / dependency hygiene'"). Cutting those
sections makes three lint tools emit dangling pointers on failure. It also makes
the "COVERED by script docstring" claim partly circular — the script's own
authority statement defers to `CLAUDE.md`.

The step-6 grep `CLAUDE.md "` misses all three: two use `CLAUDE.md under '…'`
and one uses single quotes.

**Disposition: addressed.** Grep widened to any `CLAUDE.md` reference followed
by a section name in either quote style, plus Markdown anchors. The three
messages are repointed in step 5.

### C-19 — full-scope checks omit the repo's own docs-consolidation gate · non-blocking

**Evidence.** `.trellis/spec/amc/backend/index.md:96` § Quality Check prescribes
the minimum commands for docs-only Trellis/spec consolidation (`get_context.py`,
a Trellis placeholder scan, a Markdown-link check). `docs/topology.md:74`
contains a live Markdown anchor `../CLAUDE.md#topology-graph` pointing at a
section scheduled for removal — exactly what a link check catches.

**Disposition: addressed.** Added to step 6, and the topology anchor to step 5.

## Round 2 result

Nine concerns, eight blocking, all addressed by artifact changes; one round-1
rebuttal (C-5) reversed. Two rounds of remediation have now run, which is the
contract's limit for automatic rounds.

Per contract §4, planning stops here and the user decides whether the
remediated plan is ready or a third round is warranted. `task.py start` has
not been run.

## Codex lane status

**Completed** (round 2, exit 0). The round-1 launch hung on unredirected stdin
and was killed; the re-run with `</dev/null` completed against the remediated
artifact set. Findings are C-11 through C-19 above. Codex materially disagreed
with the host lane on C-5 and was correct.
