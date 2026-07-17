# Registry-callback singleton: document + guard — Implementation Plan

## Execution Order

1. Branch from `main`. Locate the configure calls (legacy.py:8555,
   :8594-8597 — re-verify lines) and the accessor blocks in both
   modules.
2. Write the guard test first (it documents the semantics): fresh-copy
   repoint proof, original-module-patch-invisible proof,
   `finally`-restore via the original module's configure entrypoints.
   Run it twice consecutively + under `-n 4`.
3. Add the module-docstring constraint paragraphs; extend CLAUDE.md's
   callback-wiring paragraph (constraint + test pointer).
4. Draft PR → checklist (test-hygiene: the restore discipline is the
   reviewable point) → ready → merge.

## Validation Plan

```bash
.venv/bin/pytest tests/test_registry_callback_singleton.py \
  tests/test_registry_callback_singleton.py -n 0    # twice: leak check
.venv/bin/pytest tests/test_registry_callback_singleton.py -n 4
.venv/bin/pytest tests/test_validate_output.py tests/test_schema_file.py -n 0
.venv/bin/pytest && .venv/bin/pre-commit run --all-files
```

## Documentation And Spec Updates

- CLAUDE.md callback-wiring paragraph; module docstrings (the
  deliverable).

## Review Notes

- PR body cites the PRD's recorded decision so the "why not
  instance-key it" question is pre-answered; the restore-in-finally
  pattern is the reviewer focus.

## Follow-Ups

- Instance-keying only on the recorded revisit trigger, executed inside
  `07-17-audit-typed-boundaries`' signature work.
