#!/usr/bin/env python3
"""Record a Trellis session journal entry without template placeholders.

Trellis' ``add_session.py`` seeds ``(Add test results)`` and ``(see git
log)`` placeholders and auto-commits them, while the pack's review
preflight rejects placeholders in completed sessions. This wrapper closes
the gap in one shot: it resolves each commit's subject from git (failing
fast on unknown hashes), passes the Main Changes body through
``--content-file``, patches the Testing section and commit table in the
freshly written entry, verifies no placeholders remain, and only then
commits the workspace.

Exit codes:

* ``0`` - entry recorded (and committed unless ``--no-commit``).
* ``1`` - the entry could not be completed (placeholders remain, patch
  anchors missing, or the Trellis script failed).
* ``2`` - argument or environment error (unknown commit hash, missing
  Trellis script, not a git repository).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ADD_SESSION = Path(".trellis/scripts/add_session.py")
WORKSPACE = ".trellis/workspace"
PLACEHOLDERS = ("(Add details)", "(Add test results)", "(see git log)")


def run_git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def commit_subject(commit_hash: str) -> str | None:
    result = run_git("log", "-1", "--format=%s", commit_hash, "--")
    if result.returncode != 0:
        return None
    subject = result.stdout.strip().splitlines()
    return subject[0] if subject else None


def modified_workspace_journals() -> list[Path]:
    result = run_git("status", "--porcelain", "--", WORKSPACE)
    journals = []
    for line in result.stdout.splitlines():
        path_text = line[3:].strip()
        if path_text.endswith(".md") and "/journal-" in path_text:
            journals.append(Path(path_text))
    return journals


def patch_last_session(
    journal: Path,
    title: str,
    subjects: dict[str, str],
    tests: list[str],
    next_steps: list[str],
) -> str | None:
    """Patch the freshly appended session in place; return an error or None."""
    try:
        text = journal.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError) as exc:
        return f"cannot read {journal}: {exc}"
    marker = f": {title}\n"
    heading_at = text.rfind(marker)
    if heading_at == -1:
        return f"could not find the new session heading for {title!r} in {journal}"
    block_start = text.rfind("\n## Session ", 0, heading_at)
    if block_start == -1:
        return f"could not find the session block start in {journal}"
    block = text[block_start:]

    for commit_hash, subject in subjects.items():
        placeholder_row = f"| `{commit_hash}` | (see git log) |"
        if placeholder_row not in block:
            return f"missing commit table row for {commit_hash} in {journal}"
        cell = subject.replace("|", "\\|")
        block = block.replace(placeholder_row, f"| `{commit_hash}` | {cell} |", 1)

    tests_placeholder = "- [OK] (Add test results)"
    if tests_placeholder not in block:
        return f"missing Testing placeholder in {journal}"
    block = block.replace(tests_placeholder, "\n".join(tests), 1)

    if next_steps:
        default_next = "- None - task complete"
        if default_next in block:
            block = block.replace(default_next, "\n".join(next_steps), 1)

    remaining = [p for p in PLACEHOLDERS if p in block]
    if remaining:
        return f"placeholders remain after patching {journal}: {', '.join(remaining)}"

    try:
        journal.write_text(text[:block_start] + block, encoding="utf-8")
    except OSError as exc:
        return f"cannot write {journal}: {exc}"
    return None


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Record a complete Trellis session journal entry."
    )
    parser.add_argument("--title", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--commit", default="", help="Comma-separated commit hashes")
    parser.add_argument(
        "--change",
        action="append",
        required=True,
        help="Main Changes bullet (repeatable); '- ' is added when missing",
    )
    parser.add_argument(
        "--test",
        dest="tests",
        action="append",
        required=True,
        help=(
            "Testing line (repeatable): '- '-prefixed lines pass through, "
            "'[...]'-marked lines are bulleted, bare lines get '- [OK] '"
        ),
    )
    parser.add_argument(
        "--next-step",
        dest="next_steps",
        action="append",
        default=[],
        help="Next Steps bullet (repeatable); defaults to task-complete",
    )
    parser.add_argument("--branch", help="Passed through to add_session.py")
    parser.add_argument(
        "--no-commit",
        action="store_true",
        help="Leave the workspace changes uncommitted",
    )
    args = parser.parse_args(argv[1:])

    toplevel = run_git("rev-parse", "--show-toplevel")
    if toplevel.returncode != 0:
        print("error: not a git repository", file=sys.stderr)
        return 2
    # Normalize to the repository root so the relative Trellis paths and
    # git pathspecs resolve when invoked from a subdirectory.
    os.chdir(toplevel.stdout.strip())
    if not ADD_SESSION.is_file():
        print(f"error: {ADD_SESSION} not found; is Trellis initialized?", file=sys.stderr)
        return 2

    commit_arg = args.commit.strip()
    if commit_arg == "-":
        # add_session.py's explicit no-commits sentinel.
        commit_arg = ""
    hashes = [h.strip() for h in commit_arg.split(",") if h.strip()]
    subjects: dict[str, str] = {}
    for commit_hash in hashes:
        subject = commit_subject(commit_hash)
        if subject is None:
            print(f"error: unknown commit hash: {commit_hash}", file=sys.stderr)
            return 2
        subjects[commit_hash] = subject

    def as_bullet(line: str) -> str:
        stripped = line.strip()
        return line if stripped.startswith("- ") else f"- {stripped}"

    def as_test_line(line: str) -> str:
        stripped = line.strip()
        if stripped.startswith("- "):
            return line
        if stripped.startswith("["):
            # Already carries a status marker ([WARN], [SKIP], ...);
            # do not stamp [OK] over it.
            return f"- {stripped}"
        return f"- [OK] {stripped}"

    changes = [as_bullet(c) for c in args.change]
    tests = [as_test_line(t) for t in args.tests]
    next_steps = [as_bullet(n) for n in args.next_steps]

    before = set(modified_workspace_journals())
    with tempfile.NamedTemporaryFile(
        "w", suffix=".md", delete=False, encoding="utf-8"
    ) as handle:
        handle.write("\n".join(changes) + "\n")
        content_file = Path(handle.name)
    try:
        command = [
            sys.executable,
            str(ADD_SESSION),
            "--title",
            args.title,
            "--summary",
            args.summary,
            "--content-file",
            str(content_file),
            "--no-commit",
        ]
        if hashes:
            command.extend(["--commit", ",".join(hashes)])
        if args.branch:
            command.extend(["--branch", args.branch])
        result = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        if result.returncode != 0:
            # Operator-facing tool: surface the Trellis script's own output
            # (missing developer init, index marker issues, ...).
            if result.stdout:
                print(result.stdout, file=sys.stderr)
            print(
                f"error: add_session.py exited {result.returncode}",
                file=sys.stderr,
            )
            return 1
    finally:
        content_file.unlink(missing_ok=True)

    journals = [j for j in modified_workspace_journals() if j not in before] or (
        modified_workspace_journals()
    )
    if len(journals) != 1:
        # A journal dirtied before the run makes the before/after set
        # ambiguous; the entry we just wrote is the one carrying the title.
        marker = f": {args.title}\n"
        titled = []
        for j in journals:
            try:
                if marker in j.read_text(encoding="utf-8", errors="strict"):
                    titled.append(j)
            except (OSError, UnicodeError):
                continue
        if len(titled) == 1:
            journals = titled
    if len(journals) != 1:
        print(
            "error: expected exactly one modified journal file, found: "
            + (", ".join(str(j) for j in journals) or "none"),
            file=sys.stderr,
        )
        return 1

    error = patch_last_session(
        journals[0], args.title, subjects, tests, next_steps
    )
    if error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    if args.no_commit:
        print(f"Recorded session in {journals[0]} (not committed).")
        return 0

    # Stage only what this run wrote: the journal entry plus the sibling
    # index.md that add_session.py maintains. A bare `git add` on the
    # workspace would sweep unrelated dirty files into the commit.
    stage = [journals[0], journals[0].parent / "index.md"]
    stage_args = [str(path) for path in stage if path.exists()]
    if run_git("add", "--", *stage_args).returncode != 0:
        print("error: git add failed", file=sys.stderr)
        return 1
    commit = subprocess.run(
        ["git", "commit", "-m", "chore: record journal", "--", *stage_args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if commit.returncode != 0:
        print(commit.stdout, file=sys.stderr)
        print("error: git commit failed", file=sys.stderr)
        return 1

    print(f"Recorded session in {journals[0]} and committed the journal entry.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
