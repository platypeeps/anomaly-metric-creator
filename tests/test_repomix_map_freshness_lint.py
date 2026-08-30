"""Acceptance tests for `tools/check_repomix_map_freshness.py`.

The lint asserts that every path listed in the generated Repomix structural map
still exists in the tracked tree. Nothing regenerates that map automatically, so
it goes stale whenever files move without it.

The fixtures build their own synthetic repositories, so the shape under test is
"a whole directory moved out from under its map entries" — the general case —
rather than a claim about any particular tree. Work items under `docs/work/` are
the live instance of it: archiving one moves a whole directory, and the map is
regenerated in the same commit.

Pin the behaviors the script promises in its docstring:

- a map whose entries all resolve exits `0`;
- a stale entry exits `1`, and the diagnostic names the file, the line, and the
  regeneration command -- covered for the archive-move shape, for a stale path
  outside the archived tree, and for a directory whose subtree is gone;
- resolution is against the **git index**, not the filesystem: a path present on
  disk but untracked is still stale;
- a missing section, malformed indentation, a skipped level, a `..` component,
  an empty listing, an unreadable file, and a directory where `git ls-files`
  cannot run each exit `2` (structural error, distinct from staleness);
- diagnostics cite repo-relative paths on both the `1` and `2` paths, so a
  finding does not carry the runner's home directory into a CI log;
- the enumerated stale list is capped and the suppressed count is stated;
- the *actual* repo map is current right now (regression guard on the live
  artifact).

Structurally parallel to `tests/test_csv_formula_trigger_lockstep_lint.py`.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "check_repomix_map_freshness.py"
# The live artifact the regression guard below reads. A named constant so the
# resource-cost guard can see this file reads the real map, not only fixtures.
LIVE_MAP = REPO_ROOT / "docs" / "repomix-map.md"


def _run(
    *args: str, cwd: Path | None = None, ceiling: Path | None = None
) -> subprocess.CompletedProcess:
    """Run the guard. `ceiling` sets GIT_CEILING_DIRECTORIES so git's upward
    search for a repository stops there — without it, whether a fixture
    directory looks like a git repo depends on where pytest's tmp_path happens
    to live on the machine running the suite."""
    env = None
    if ceiling is not None:
        env = {**os.environ, "GIT_CEILING_DIRECTORIES": str(ceiling)}
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=None if cwd is None else str(cwd),
        env=env,
    )


def _map_text(tree: str) -> str:
    """Wrap a directory-structure body in the surrounding map document.

    `tree` is spliced verbatim so a test can supply malformed indentation."""
    return (
        "This file is a merged representation of a subset of the codebase.\n"
        "\n"
        "# File Summary\n"
        "\n"
        "## Notes\n"
        "- Files matching patterns in .gitignore are excluded\n"
        "\n"
        "# Directory Structure\n"
        "````\n"
        f"{tree}\n"
        "````\n"
        "\n"
        "# Repository Files\n"
    )


def _repo(tmp_path: Path, files: list[str], tree: str) -> tuple[Path, Path]:
    """Build a real git repo containing `files`, with a map holding `tree`.

    A genuine repo rather than a stub: the check reads `git ls-files`, so a fake
    would test the wrong thing entirely."""
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    for relative in files:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=root,
        check=True,
    )
    map_path = root / "docs" / "repomix-map.md"
    map_path.write_text(_map_text(tree), encoding="utf-8")
    return root, map_path


CLEAN_TREE = "\n".join(
    [
        "docs/",
        "  repomix-map.md",
        "src/",
        "  app.py",
    ]
)
CLEAN_FILES = ["docs/repomix-map.md", "src/app.py"]


def test_current_map_exits_zero(tmp_path: Path) -> None:
    _, map_path = _repo(tmp_path, CLEAN_FILES, CLEAN_TREE)
    result = _run(str(map_path))
    assert result.returncode == 0, result.stderr
    assert "repomix map is current" in result.stdout


def test_archive_move_shape_exits_one(tmp_path: Path) -> None:
    """The PR #381 shape: the work item moved under archive/, the map still names
    the pre-archive path. This is the structural recurrence the guard exists
    for."""
    tree = "\n".join(
        [
            "docs/",
            "  repomix-map.md",
            "  work/",
            "    2026-08-15-some-item/",
            "      prd.md",
        ]
    )
    files = [
        "docs/repomix-map.md",
        "docs/work/archive/2026-08/2026-08-15-some-item/prd.md",
    ]
    _, map_path = _repo(tmp_path, files, tree)
    result = _run(str(map_path))
    assert result.returncode == 1
    assert "docs/work/2026-08-15-some-item/prd.md" in result.stderr
    assert "update_repomix" in result.stderr


def test_stale_entry_outside_the_archived_tree_exits_one(tmp_path: Path) -> None:
    """A stale path anywhere else is the same defect and must be caught the same
    way; the guard is not scoped to the tree that archives."""
    tree = "\n".join(["docs/", "  repomix-map.md", "scripts/", "  gone.sh"])
    _, map_path = _repo(tmp_path, ["docs/repomix-map.md"], tree)
    result = _run(str(map_path))
    assert result.returncode == 1
    assert "scripts/gone.sh" in result.stderr


def test_directory_entry_with_no_tracked_children_exits_one(tmp_path: Path) -> None:
    tree = "\n".join(["docs/", "  repomix-map.md", "removed/", "  child.py"])
    _, map_path = _repo(tmp_path, ["docs/repomix-map.md"], tree)
    result = _run(str(map_path))
    assert result.returncode == 1
    assert "removed" in result.stderr


def test_untracked_file_on_disk_is_still_stale(tmp_path: Path) -> None:
    """Resolution is against the git index, not the filesystem. A stale entry
    that happens to match an untracked working-tree file must still fail, or the
    check would pass locally and fail in CI for exactly this case."""
    tree = "\n".join(["docs/", "  repomix-map.md", "src/", "  untracked.py"])
    root, map_path = _repo(tmp_path, ["docs/repomix-map.md"], tree)
    present = root / "src" / "untracked.py"
    present.parent.mkdir(parents=True, exist_ok=True)
    present.write_text("x\n", encoding="utf-8")
    assert present.is_file(), (
        "the file must exist on disk for this test to mean anything"
    )
    result = _run(str(map_path))
    assert result.returncode == 1
    assert "src/untracked.py" in result.stderr


def test_missing_directory_structure_section_exits_two(tmp_path: Path) -> None:
    root, map_path = _repo(tmp_path, CLEAN_FILES, CLEAN_TREE)
    map_path.write_text("# File Summary\n\nnothing here\n", encoding="utf-8")
    result = _run(str(map_path))
    assert result.returncode == 2
    assert "Directory Structure" in result.stderr


def test_odd_indentation_exits_two(tmp_path: Path) -> None:
    tree = "\n".join(["docs/", "   repomix-map.md"])
    _, map_path = _repo(tmp_path, CLEAN_FILES, tree)
    result = _run(str(map_path))
    assert result.returncode == 2
    assert "multiple of two" in result.stderr


def test_skipped_indent_level_exits_two(tmp_path: Path) -> None:
    tree = "\n".join(["docs/", "    deep.py"])
    _, map_path = _repo(tmp_path, CLEAN_FILES, tree)
    result = _run(str(map_path))
    assert result.returncode == 2
    assert "skips an indentation level" in result.stderr


def test_parent_traversal_component_exits_two(tmp_path: Path) -> None:
    """A `..` entry is malformed, not stale: a generator never emits one, and
    treating it as drift would send the author to regenerate an artifact whose
    real problem is its shape."""
    tree = "\n".join(["..", "docs/", "  repomix-map.md"])
    _, map_path = _repo(tmp_path, CLEAN_FILES, tree)
    result = _run(str(map_path))
    assert result.returncode == 2
    assert "stay inside" in result.stderr


def test_empty_listing_exits_two(tmp_path: Path) -> None:
    root, map_path = _repo(tmp_path, CLEAN_FILES, CLEAN_TREE)
    map_path.write_text(_map_text(""), encoding="utf-8")
    result = _run(str(map_path))
    assert result.returncode == 2
    assert "lists no entries" in result.stderr


def test_unreadable_map_exits_two(tmp_path: Path) -> None:
    root, _ = _repo(tmp_path, CLEAN_FILES, CLEAN_TREE)
    result = _run(str(root / "docs" / "absent.md"))
    assert result.returncode == 2
    assert "cannot read" in result.stderr


def test_many_stale_entries_are_capped_and_the_remainder_counted(
    tmp_path: Path,
) -> None:
    """A wholesale regeneration can strand hundreds of paths. Printing them all
    buries the diagnostic; printing some without saying so implies the list is
    complete."""
    gone = [f"  gone{n}.py" for n in range(45)]
    tree = "\n".join(["docs/", "  repomix-map.md", "src/", *gone])
    _, map_path = _repo(tmp_path, ["docs/repomix-map.md"], tree)
    result = _run(str(map_path))
    assert result.returncode == 1
    assert "further stale path(s) not listed" in result.stderr
    assert result.stderr.count("which is not tracked") == 20


def test_diagnostics_cite_a_repo_relative_path_not_an_absolute_one(
    tmp_path: Path,
) -> None:
    """A stale-entry diagnostic is read in a CI log and pasted into a review
    thread. An absolute path there leaks the runner's or the author's home
    directory and makes the same finding read differently on every machine."""
    tree = "\n".join(["docs/", "  repomix-map.md", "scripts/", "  gone.sh"])
    root, map_path = _repo(tmp_path, ["docs/repomix-map.md"], tree)
    result = _run(str(map_path))
    assert result.returncode == 1
    assert "docs/repomix-map.md:" in result.stderr
    assert str(root) not in result.stderr
    assert str(tmp_path) not in result.stderr


def test_structural_diagnostics_also_avoid_absolute_paths(tmp_path: Path) -> None:
    """The exit-2 paths build their messages separately from the exit-1 path, so
    redaction has to hold on both or it holds on neither in practice."""
    tree = "\n".join(["..", "docs/", "  repomix-map.md"])
    root, map_path = _repo(tmp_path, CLEAN_FILES, tree)
    result = _run(str(map_path))
    assert result.returncode == 2
    assert str(root) not in result.stderr


def test_unreadable_map_diagnostic_does_not_echo_the_absolute_path(
    tmp_path: Path,
) -> None:
    """`OSError` renders as "[Errno 2] ...: '<absolute path>'", so reporting the
    exception whole would reintroduce exactly what the display helper strips."""
    root, _ = _repo(tmp_path, CLEAN_FILES, CLEAN_TREE)
    result = _run(str(root / "docs" / "absent.md"))
    assert result.returncode == 2
    assert "cannot read docs/absent.md" in result.stderr
    assert str(root) not in result.stderr


def test_non_utf8_map_exits_two_without_a_traceback(tmp_path: Path) -> None:
    """`UnicodeDecodeError` is a `ValueError`, not an `OSError`, so the read's
    `except OSError` clause does not catch it. Uncaught it escaped the handler
    in `main` and exited **1** with a traceback -- which under this script's own
    contract asserts the map is *stale*, the one thing a decode failure has not
    shown, while printing an absolute path the redaction rule forbids."""
    root, map_path = _repo(tmp_path, CLEAN_FILES, CLEAN_TREE)
    map_path.write_bytes(b"# Directory Structure\n\xff\xfe not utf-8\ndocs/\n")

    result = _run(str(map_path))
    assert result.returncode == 2
    assert "not valid UTF-8" in result.stderr
    assert "Traceback" not in result.stderr
    assert str(root) not in result.stderr


def test_non_git_directory_exits_two(tmp_path: Path) -> None:
    """`git ls-files` failing is a structural error, not staleness: nothing has
    been shown about whether the map is current.

    The map has to be *readable* here, or the run exits 2 at the read and never
    reaches the git call — which is how the first version of this test passed
    while covering nothing."""
    root = tmp_path / "not-a-repo"
    (root / "docs").mkdir(parents=True)
    map_path = root / "docs" / "repomix-map.md"
    map_path.write_text(_map_text(CLEAN_TREE), encoding="utf-8")
    assert map_path.is_file()

    result = _run(str(map_path), cwd=root, ceiling=tmp_path)
    assert result.returncode == 2
    # Naming the branch, not just the exit code: "cannot read" here would mean
    # the test had regressed to its earlier no-op form.
    assert "git ls-files exited" in result.stderr
    assert "cannot read" not in result.stderr


def test_option_like_argument_is_treated_as_a_path_and_exits_two() -> None:
    result = _run("--not-a-flag")
    assert result.returncode == 2
    assert "cannot read" in result.stderr


def test_too_many_arguments_exits_two() -> None:
    result = _run("a", "b")
    assert result.returncode == 2
    assert "usage:" in result.stderr


def test_help_exits_zero() -> None:
    result = _run("--help")
    assert result.returncode == 0
    assert "repomix" in result.stdout.lower()


def test_live_repository_map_is_current() -> None:
    """Regression guard on the real artifact, not fixtures: this is the
    assertion that fails when someone moves files without regenerating."""
    assert LIVE_MAP.is_file()
    result = _run()
    assert result.returncode == 0, result.stderr
    assert "repomix map is current" in result.stdout
