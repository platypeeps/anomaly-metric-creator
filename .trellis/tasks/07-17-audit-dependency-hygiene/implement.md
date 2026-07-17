# Dependency hygiene — Implementation Plan

## Execution Order

1. Branch from `main`. A-043 verification first (it decides the diff):
   scratch-remove the npm dependency, launch OpenCode against the repo,
   record tolerate/require. Apply the matching branch (remove + drop npm
   Dependabot entry | pin exact + commit lockfile + keep entry).
2. A-044: "Pinned tools bump" checklist subsection in
   docs/DEVELOPMENT_CYCLE.md; cross-reference from the pre-PR CI-hygiene
   heading (CLAUDE.md + template stay untouched unless a heading is
   added — it is not; this is body guidance under the existing heading).
3. A-045: identify the canonical security-skill copy; add the provenance
   header (upstream URL, vendored ref, refresh procedure); fan out to the
   five copies; README sentence. If pack-owned → repo-docs note +
   paste-ready upstream suggestion instead.
4. Flip A-043/A-044/A-045 → `fixed` in the ledger (same PR).
5. Draft PR → checklist (dependency-hygiene heading) → ready → merge.

## Validation Plan

```bash
grep -rn "opencode-ai/plugin" . --exclude-dir=.git      # per chosen branch
ls .opencode/*lock* 2>/dev/null                          # pin branch only
sha256sum $(rg --files -g 'SKILL.md' | rg security-best-practices)  # 5 identical
.venv/bin/pytest -m "not heavy" -n 2   # cheap sanity; no code paths touched
.venv/bin/pre-commit run --all-files
```

## Documentation And Spec Updates

- docs/DEVELOPMENT_CYCLE.md (bump checklist), README (skill provenance
  sentence + none/updated npm mention).

## Review Notes

- Lead the PR description with the OpenCode verification transcript — the
  whole A-043 decision hangs on it.

## Follow-Ups

- If the pin-branch was taken: consider folding the lockfile into the
  pack-sync automation (ci-cadence PR 3) later.
