# Local gate dedupe — Implementation Plan

## Execution Order

1. Branch from `main`. Time the deterministic gate first so the PR has a
   before number:
   ```bash
   time .venv/bin/node scripts/check-review-preflight.mjs   # or: node scripts/...
   time .venv/bin/python3 scripts/sd-ai-command-pack-install-audit.py
   time node scripts/sd-ai-command-pack-review-preflight.mjs
   ```
2. **Part A.** Remove the duplicate pytest bundle from
   `scripts/check-review-preflight.mjs` and retain comments naming
   `tests/test_copilot_instruction_contract.py` and
   `tests/test_pr_body_scope_lint.py` — the two that
   `_check_review_preflight_wiring`
   requires the file to mention. Preserve the three contract guards and the
   canonical mypy gate; current component timing puts those four direct checks
   at about 0.26s combined.
3. Immediately verify the pins still hold — this is the step that catches a
   bad trim:
   ```bash
   .venv/bin/python3 tools/check_copilot_instruction_contract.py
   .venv/bin/python3 tools/check_ci_review_contract.py
   .venv/bin/pytest tests/test_copilot_instruction_contract.py tests/test_ci_review_contract.py -n 0
   ```
   All four must pass. A failure here means a pinned path was dropped.
4. Re-time the gate; confirm the deterministic total is at or below ~2.5s.
5. **Parts B and C.** File the Prism triple-invocation and KB self-heal
   items upstream against sd-ai-command-pack. Record the upstream
   references in this task's `prd.md`. **Do not edit the pack-managed files
   locally.**
6. Document the two workarounds in `docs/DEVELOPMENT_CYCLE.md`:
   `SD_AI_COMMAND_PACK_FULL_CHECK_PRISM=0` as the fast path, and the KB
   regen command for when the freshness gate trips after a pull:
   ```bash
   .venv/bin/python3 scripts/sd-ai-command-pack-update-spec-kb.py
   ```
7. Confirm no pack drift:
   ```bash
   .venv/bin/python3 scripts/sd-ai-command-pack-install-audit.py
   ```
8. Draft PR -> pre-PR checklist (CI/workflow hygiene, doc sync) -> ready ->
   merge.

## Validation Plan

```bash
# the trimmed script itself
node scripts/check-review-preflight.mjs && echo "preflight OK"

# both contract guards against the real repo — the pins must still hold
.venv/bin/python3 tools/check_ci_review_contract.py
.venv/bin/python3 tools/check_copilot_instruction_contract.py

# their real-repo tests
.venv/bin/pytest tests/test_ci_review_contract.py tests/test_copilot_instruction_contract.py -n 0

# no pack-managed file drifted
.venv/bin/python3 scripts/sd-ai-command-pack-install-audit.py

# whole gate
bash scripts/sd-ai-command-pack-full-check.sh
.venv/bin/pre-commit run --all-files
```

Negative check worth doing once: temporarily remove
`tests/test_pr_body_scope_lint.py` from the trimmed list and confirm
`tools/check_copilot_instruction_contract.py` **fails**. That proves the pin
is real and the trim was bounded by it rather than by guesswork. Restore.

## Documentation And Spec Updates

- `docs/DEVELOPMENT_CYCLE.md` — Prism opt-out, KB regen command.
- This task's `prd.md` — upstream issue references for Parts B and C.
- No `CLAUDE.md` change expected; if it describes the preflight's contents,
  update it in the same diff.

## Review Notes

- Explain why this is a **trim, not a deletion**, and name the pin
  (`REQUIRED_FILES` at `tools/check_copilot_instruction_contract.py:32`
  plus `check()` at `:404`). Without that, "the script is fully redundant,
  so I trimmed it" invites the obvious "then why not delete it?".
- Correct the record on the two preflights: they do different jobs. The pack
  one validates review references; the legacy one runs contract guards and
  lint tests. Earlier framing called the pack one a replacement — it is not,
  and the removal argument rests on CI coverage instead.
- Show the before/after gate timing.
- State plainly that Parts B and C are filed upstream and not implemented
  here, so a reviewer does not look for them in the diff.

## Follow-Ups

- Full removal of `scripts/check-review-preflight.mjs` — a ~15-file change
  across both contract guards, four test files' fixtures, and six doc
  references (two pack-managed). Worth raising upstream, since the pack's
  own guards are what mandate the redundancy. Do not attempt as a local
  cleanup.
- Track the upstream Prism and KB issues to closure; until they land, the
  documented workarounds are the whole mitigation.
