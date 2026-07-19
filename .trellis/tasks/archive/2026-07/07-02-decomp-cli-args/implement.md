# Extract cli_args.py — Implementation Plan

## Execution Order

1. Branch from `main`. Pre-flight greps (paste results into the PR):
   - `grep -rn "monkeypatch.setattr" tests/ | grep -E
     "COMPONENTS|SCENARIOS|DEFAULT_METRICS_PER_COMPONENT"` (registries —
     expect hits; they justify the seam),
   - same grep over the ~14 plain constants (expect zero hits; any hit
     upgrades that constant to a getter),
   - callers of `_parse_start_time_arg` / `_sig` / `_flag_in_argv`
     outside the CLI cluster (expect zero; else move-with-callers).
2. Capture baselines: `--help`, `--help-all`, and `amc serve --help`
   output to files for the byte-diff in step 6.
3. Create `cli_args.py`: seam (`_configure_cli_runtime` + RuntimeError
   guard), then the verbatim-moved cluster. Resolve the serve executor's
   legacy-module access per design.md (seam getter or dispatch argument).
4. Edit `legacy.py`: delete the moved range (grep it for `^from \.`
   re-imports first — splice hazard), add the re-import block with
   `as`-aliases, add the configure call directly beneath it.
5. Measure `wc -l cli_args.py`; if >800, split the four executors into
   `cli_subcommands.py` (same PR) per design.md.
6. Validate: full suite; byte-diff the three help outputs against step 2;
   run the named CLI test files serially first for fast signal.
7. CLAUDE.md module map + spec index update in the same commit.
8. Draft PR (`full-ci` label) → pre-PR checklist → ready → merge.
9. Update the epic checklist (step 8 done) in the parent task.

## Validation Plan

```bash
.venv/bin/pytest tests/test_cli_surface.py tests/test_args.py tests/test_cli.py -n 0
.venv/bin/pytest tests/test_server.py -n 0        # serve flag forwarding
.venv/bin/pytest                                   # full suite = hashes
diff /tmp/help-before.txt <(python anomaly-metric-creator.py --help)
.venv/bin/pre-commit run --all-files
```

## Documentation And Spec Updates

- CLAUDE.md: module map + a sentence documenting `_configure_cli_runtime`
  next to the schema_impl callback precedent.
- Epic design.md: tick step 8 in the Status section.

## Review Notes

- PR description must state: verbatim move, seam only, zero test edits,
  zero help-text drift (attach the diff evidence). This is the epic's most
  contract-dense step — reviewers should see the checklist receipts.

## Follow-Ups

- Step 9 (`07-02-decomp-catalog-data`) picks up the resolution cluster and
  can later simplify the seam (cli_args may import `catalog.py` constants
  directly once they exist — do NOT do it preemptively here).
