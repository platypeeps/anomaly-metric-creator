---
name: sd-update-spec
description: Use when the user wants the Software Delivery update-spec command to run Trellis update-spec and the pack's extended spec-refresh features.
---

# SD Update Spec

Run the Trellis update-spec workflow for the current repository, then run the SD
AI command pack extensions. These extensions preserve repository knowledge that
the base Trellis skill does not own directly: repospec/Repomix refreshes,
architecture overview touch-ups when warranted, and `.obsidian-kb` copies for
portable Obsidian knowledge-base workflows.

1. Resolve the `trellis-update-spec` skill by name using the agent's trusted
   skill discovery mechanism for installed skills.
2. If the Trellis update-spec skill is missing, unreadable, empty, resolves to
   more than one candidate, fails validation, defines contradictory steps that
   violate this command's safety rules, or requires unavailable tools, stop and
   report the exact blocker.
3. Use the Trellis update-spec skill as the primary instructions. Do not
   modify, replace, fork, or reinterpret it. It is responsible for deciding
   what `.trellis/spec/` content should change.
4. After the Trellis update-spec pass, run the SD AI command pack extensions:
   - Check whether the repo has infrastructure for maintaining a repospec
     artifact in this order: a `Makefile` target named `repospec`,
     `update-repospec`, `refresh-repospec`, `repomix`, `update-repomix`, or
     `refresh-repomix`; a `package.json` script with one of those exact names;
     an executable script under `scripts/` named `repospec`, `update-repospec`,
     `refresh-repospec`, `repomix`, `update-repomix`, `refresh-repomix`,
     `repo-map`, `update-repo-map`, or `refresh-repo-map` with an optional
     `.sh`, `.py`, `.js`, `.mjs`, or `.ts` extension; then a documented command
     under a heading named `Repospec`, `Repomix`, or `Repository map` in
     `AGENTS.md` or `README.md`. Do not infer commands from incidental prose.
     If that infrastructure exists, use it to refresh the repospec artifact
     instead of hand-editing generated output. Do not create new repospec
     infrastructure or a new repospec artifact unless the user asks. When the
     repospec refresh uses Repomix or another repository-map tool, follow the
     target repo's documented output path. If no path is documented, prefer
     `docs/repomix-map.md` and report the chosen path.
   - Check whether the repo already has an architectural overview. Search
     existing files, especially `ARCHITECTURE.md`, `ARCHITECTURE_OVERVIEW.md`,
     `docs/ARCHITECTURE.md`, `docs/ARCHITECTURE_OVERVIEW.md`, and
     `.trellis/spec/**/architecture*.md`. Do not create a new overview unless
     the user asks for one.
   - If an overview exists, update it only when the preserved work changed one
     of these architecture signals: package/module boundaries, service or
     command surfaces, cross-component data flow, persistence/storage schemas,
     external integrations, config/env contracts, or runtime/deployment
     topology. Use concrete evidence from changed files, Trellis specs, or
     task notes. If no overview exists, or if none of those signals changed,
     leave it untouched. If the scope of the completed work is unclear, report
     that and leave the overview unchanged unless the user confirms the
     intended scope.
   - Refresh the repo-local Obsidian knowledge-base folder through the pack
     helper:
     - Run `python3 scripts/sd-ai-command-pack-update-spec-kb.py` from the repo
       root. If the helper is missing or exits nonzero, stop and report the
       command, exit status, and complete stdout/stderr output; do not rebuild
       `.obsidian-kb/` manually from this wrapper.
     - Treat the helper as the source of truth for `.obsidian-kb/`: it owns the
       managed entry in the repo root `.gitignore`, creates or refreshes the
       generated folder, copies repo-knowledge files into visible semantic
       category folders, writes `.obsidian-kb/Dashboard - <repo>.md` as a
       landing page, `.obsidian-kb/LLM-KB - <repo>.md` as a self-contained LLM
       overview, skips secrets/caches/build output and `.trellis/workspace/`,
       avoids generated file/folder names that start with `.` or use
       Trellis-specific naming, and reports conflicts. Helper-selected
       knowledge files include repository docs and workflow/spec context such
       as `.trellis/workflow.md`, `.trellis/config.yaml`,
       `.trellis/spec/**/*.md`, `.trellis/tasks/**/*.md`, and repo-owned
       repospec or Repomix outputs such as `docs/repomix-map.md` when present.
       Do not
       manually edit `.gitignore`, create KB copies, remove stale generated
       entries, or overwrite dashboard conflicts from this wrapper.
     - Use `python3 scripts/sd-ai-command-pack-update-spec-kb.py --dry-run` when
       the user wants a preview before changing generated KB state.
5. Final report:
   - `Update-spec skill`: path read
   - `Spec updates`: paths changed, or `none`
   - `Repospec`: refreshed path/tool, `not present`, or `no infrastructure`
   - `Architectural overview`: updated path, `not present`, or `not warranted`
   - `Obsidian KB`: `.obsidian-kb` created/refreshed, copy count, dashboard
     state, gitignore state, and any conflicts
   - `Obsidian vault copy`: example command for copying this repo's
     `.obsidian-kb` folder into a vault. State that `/path/to/your/vault` is a
     placeholder the user must replace, and derive the final link name from the
     repository name when possible, such as
     `cp -R "$(pwd)/.obsidian-kb/." "/path/to/your/vault/Repo-KB"`
   - `Validation`: checks run, or why checks were not run
