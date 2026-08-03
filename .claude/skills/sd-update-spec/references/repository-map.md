# Repository-map extension

Load this direct reference only when the user explicitly requests a repospec or
repository-map refresh, or when exact repository evidence finds existing map
maintenance infrastructure. This reference does not load another reference.

Detect infrastructure in this order:

1. A `Makefile` target named `repospec`, `update-repospec`,
   `refresh-repospec`, `repomix`, `update-repomix`, or `refresh-repomix`.
2. A `package.json` script with one of those exact names.
3. An executable under `scripts/` named `repospec`, `update-repospec`,
   `refresh-repospec`, `repomix`, `update-repomix`, `refresh-repomix`,
   `repo-map`, `update-repo-map`, or `refresh-repo-map`, with an optional
   `.sh`, `.py`, `.js`, `.mjs`, or `.ts` extension.
4. A documented command under a heading named `Repospec`, `Repomix`, or
   `Repository map` in `AGENTS.md` or `README.md`.

Do not infer commands from incidental prose. If infrastructure exists, use it
instead of hand-editing generated output. Do not create infrastructure or a new
artifact unless the user asks. Follow the documented output path; when none is
documented, prefer `docs/repomix-map.md` and report that choice.

Report the refreshed path and tool, `not present`, or `no infrastructure`.
