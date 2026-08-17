# Design — repomix map freshness lint

## Investigation that changed the PRD

The PRD assumed the map's exclusion set could be derived without the `repomix`
binary. Tracing why each of the three currently-absent files is absent shows it
comes from **three different mechanisms**, not one:

| absent file | excluded by |
| --- | --- |
| `docs/repomix-map.md` | the explicit `--ignore` flag in `scripts/update_repomix` |
| `.trellis/.template-hashes.json` | root `.gitignore:35` — yet the file is **tracked anyway**, so a plain `git check-ignore` reports no match; only `--no-index` finds it |
| `uv.lock` | repomix's **built-in default ignore patterns**, named in no file in this repository |

The map is partly self-describing about this. Its Notes section records:

```
- Files matching these patterns are excluded: docs/repomix-map.md
- Files matching patterns in .gitignore are excluded
- Files matching default ignore patterns are excluded
```

So the `--ignore` set can be read from the map itself rather than by parsing
`update_repomix` — one mechanism solved. The `.gitignore` set is derivable via
`git check-ignore --no-index`. The third is not: repomix's defaults live in the
tool, and reproducing them means either depending on the binary (which the PRD
rules out, because `update_repomix` exits `127` without it) or hand-maintaining
a mirror of an upstream list — a second registry for the same fact, which is
the exact failure mode this lint exists to prevent.

## D1 — Scope: one direction now, the other deferred

The guard checks that **every path listed in the map resolves to a real tracked
path**, across all trees.

It does **not** check the reverse — a tracked file absent from the map.

Rationale. The two directions have completely different costs:

- map → repository needs **no exclusion set at all**. An entry that is in the
  map is by definition not excluded, so nothing has to be derived. Zero false
  positives by construction.
- repository → map is exactly the direction that needs the full exclusion set,
  and is therefore blocked on the opaque-defaults problem above.

The shipped direction catches the PR #381 class (the `task.py archive` move
stranding `.trellis/tasks/<slug>/` entries), which is the *structural* one: a
completion-mode ship archives the task after the map was last generated, so it
recurs by construction rather than by accident. It is also already broader than
the existing external check, which is `.trellis/`-only.

The deferred direction catches the PR #382 class (new `scripts/` files never
added to the map). That is a real gap and stays open; it is recorded as a
follow-up rather than half-implemented here. Shipping a repository → map check
with a hardcoded mirror of repomix's defaults would trade a known gap for a
silent one.

## D2 — Parse the tree, do not regex the file

The map renders its listing as an indented tree inside a fenced block under a
`# Directory Structure` heading: each level is exactly two spaces, and a
trailing `/` marks a directory, so an entry's full path is the stack of
enclosing directory names. Parse that structure and reconstruct paths from the
stack.

This is the same shape the pack's `parseGeneratedStructuralMapEntries` reads.
Matching it deliberately: two independent parsers disagreeing about what the
map says would be worse than either parser alone.

Structural failures — no `# Directory Structure` section, an indent that is not
a multiple of two, an indentation level skipped, a `..` component that would
make an existence probe stat outside the repository — exit `2`, never `1`.
Exit `1` means "the map is stale"; a map that cannot be read has not been shown
to be stale, and reporting it as such sends the author to regenerate an
artifact whose real problem is that it is malformed.

## D3 — Resolve against the git index, not the filesystem

An entry "exists" if it is a tracked path: a file in `git ls-files`, or a
directory that is the prefix of one.

Not a filesystem probe, which is what the external check uses. Two reasons:

- **Untracked local debris would mask staleness.** A stale entry that happens
  to match a leftover file in the developer's working tree passes on their
  machine and fails in CI. The index is the same in both.
- **It is the stricter and more correct question.** The map is generated from
  the tracked, gitignore-respecting tree, so the tracked set is what it claims
  to describe. If a listed path is untracked at this commit, a fresh clone of
  this commit genuinely does not have it — that is staleness, and a filesystem
  probe on the author's machine would hide it.

Measured consequence, found while running implement step 5. Repomix generates
from the **working tree**, so regenerating the map immediately after creating
files produces entries the index does not yet carry: nine of them here, dropping
to `0` after `git add -A`. The index and the working tree therefore disagree for
exactly as long as the author has not decided what to commit — and at pre-commit
time they agree by construction, because everything being committed is staged.
That is the whole argument for D3 in one observation: the filesystem variant
would have called this state clean and then failed in CI.

## D4 — Selection: the guard must run when *other* files move

The natural hook wiring — `files: ^docs/repomix-map\.md$` — is **wrong here**,
and wrong in a way that would make the guard look installed while guarding
nothing. Map staleness is not caused by editing the map; it is caused by moving
or deleting files *elsewhere* while the map stays unchanged. A `files:`-selected
hook would run on precisely the commits that cannot be stale and skip every
commit that can.

Use `always_run: true` + `pass_filenames: false`, the shape
`tools/check_branch_name.py` already uses for a diff-independent check. The
check is cheap — one file parse plus one `git ls-files` — so running it on
every commit costs nothing meaningful.

Consequence for `tools/check_guard_ci_coverage.py`: an `always_run` hook selects
no files, so this guard lands in the **unlaned** group ("no file-selecting hook
— must merely run somewhere"), beside `check_branch_name.py` and
`check_ruff_lockstep.py`. It must therefore be invoked explicitly in a CI job,
and its own test file must run in the QUICK lane so the
"lints whose own tests never run in the QUICK lane" section still prints `none`.

## D4a — Operability: this guard will block `task.py archive`

`task.py archive` **commits by itself** (`[OK] Auto-committed: chore(task):
archive <slug>`), through `run_git(["commit", "-m", ...])` in
`.trellis/scripts/common/task_store.py` — with no `--no-verify`, so a
pre-commit-stage hook does fire inside it. Since the archive move is precisely
what strands the map entries, the guard will fail that commit.

The resulting state is mild, which is what makes accepting it easy. `task.py`
does not abort or roll back: it prints `[WARN] Auto-commit failed: …` and
returns, leaving the move applied and **staged**. So the author gets a warning
and a staged archive, not a half-applied operation — one
`./scripts/update_repomix` and one `git commit` finish the job.

Verified by reading the call site rather than inferred from the CLI output.

Accepted deliberately, with the sequence documented rather than the guard
weakened:

1. `task.py archive`
2. `./scripts/update_repomix`
3. commit the archive move together with the regenerated map

`docs/DEVELOPMENT_CYCLE.md` carries this ordering (implement step 3).

Rejected alternative: restricting the hook to a `pre-push` stage, as
`check_branch_name.py` does. It would dodge the archive collision, but the
archive commit is exactly the commit that introduces the staleness, and a guard
that declines to look at the commit that breaks the thing it guards is theatre.

## D5 — Exit contract

Matches every other guard in `tools/`:

- `0` — every map entry resolves; the in-step message names the count checked.
- `1` — one or more entries are stale; the diagnostic names each `file:line`,
  the path, and the regeneration command. Cap the enumerated list and state how
  many were suppressed, so a wholesale regeneration does not print hundreds of
  lines while implying the printed set is complete.
- `2` — structural: the map is missing, unreadable, has no directory-structure
  section, or is malformed per D2.

Optional path arguments default to the repo-root map so tests can point the
check at fixtures, as `check_csv_formula_trigger_lockstep.py` does.

## D6 — Interpreter floor: run the project's, parse under anything

The project pins `requires-python = ">=3.14"` and ruff `target-version =
"py314"`, and CI invokes the guard as `uv run --python 3.14 --no-project`. That
fixes the version the guard *runs* under in CI — but not the one it runs under
locally. The pre-commit hook is `language: python` with no `language_version`,
so pre-commit builds its environment from whatever interpreter it resolves on
the contributor's machine, which is not required to be the project's 3.14.

Two consequences the implementation is held to:

- **Stdlib only, no third-party imports.** The guard needs no `additional_dependencies`
  and no `--with`, so the hook environment cannot drift from the CI one.
- **No version-gated syntax, even where the project floor permits it.** A
  3.14-only construct parses fine under `uv run --python 3.14` and fails at
  import under an older pre-commit interpreter — a failure that reads as "the
  map is broken" to whoever meets it. This cost a real round: the first draft
  used PEP 758's unparenthesized `except (OSError, ValueError):`, which is
  legal at this project's floor, but `ruff format` strips the parentheses under
  `py314` and the surviving `except OSError, ValueError:` is both 3.14-only and
  indistinguishable from a Python 2 relic. Split into two clauses instead;
  verified parsing under 3.9, 3.12, 3.13, and 3.14, and unchanged by `ruff
  format`.

The guard is therefore *pinned* to 3.14 by CI and *portable* below it by
construction. Neither alone would be enough: the pin does not reach pre-commit,
and portability alone would not stop a later edit from reintroducing a gated
construct.

## Alternatives rejected

- **Regenerate and diff.** Requires the `repomix` binary. `update_repomix` exits
  `127` without it, so this either breaks contributor machines or is skipped
  into uselessness in CI. Also makes the guard's verdict depend on the
  installed repomix version.
- **Auto-regenerate on commit.** Hides the disagreement between the author's
  tree and the map instead of reporting it, and silently rewrites a generated
  artifact mid-commit. The remedy stays one explicit `./scripts/update_repomix`.
- **Mirror repomix's default ignores in-repo** to unlock the second direction.
  A second registry for the same fact, drifting on every repomix upgrade with
  no guard of its own.

## Follow-up this design deliberately leaves open

The repository → map direction (the PR #382 class) is unguarded. Closing it
needs a decision on where repomix's default ignore set comes from. File as a
separate task; do not fold it into this PR.
