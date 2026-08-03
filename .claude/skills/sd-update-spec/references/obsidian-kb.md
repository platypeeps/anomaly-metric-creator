# Obsidian KB extension

Load this direct reference only for an explicit preview, an unusual occupied or
symlinked `.obsidian-kb` path, helper failure recovery, or detailed ownership
and report interpretation. The canonical skill owns the normal refresh command;
this reference does not load another reference.

The helper is the source of truth for `.obsidian-kb/`. It owns the managed root
`.gitignore` entry, creates or refreshes the generated folder, preserves a valid
root symlink to a directory, copies selected repository knowledge into visible
semantic categories, writes `Dashboard - <repo>.md` and `LLM-KB - <repo>.md`,
prunes stale generated entries, skips secrets/caches/build output and
`.trellis/workspace/`, and reports conflicts. Selected knowledge includes
repository docs plus `.trellis/workflow.md`, `.trellis/config.yaml`,
`.trellis/spec/**/*.md`, `.trellis/tasks/**/*.md`, and existing repo-owned map
artifacts such as `docs/repomix-map.md`.

Do not manually edit the managed ignore block, create KB copies, remove stale
generated entries, replace root symlinks, or overwrite dashboard conflicts from
the wrapper. For a requested preview, run:

```bash
bash scripts/sd-ai-command-pack-toolchain.sh run-python -- \
  scripts/sd-ai-command-pack-update-spec-kb.py --dry-run
```

If the helper is missing or exits nonzero, stop and report the exact command,
exit status, and complete stdout/stderr output. Do not rebuild the KB manually.
For a valid result, report create/refresh and copy counts, dashboard state,
gitignore state, conflicts, and the placeholder vault-copy example required by
the canonical skill.
