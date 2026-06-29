"""Acceptance tests for `tools/check_trellis_placeholders.py`.

Finish-work task and workspace artifacts are committed repo files. This lint
keeps template placeholders from landing in journals or task notes, and keeps
workspace journal commit lists aligned with their index rows.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "check_trellis_placeholders.py"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
    )


def _artifact(tmp_path: Path, text: str) -> str:
    path = tmp_path / "journal.md"
    path.write_text(text, encoding="utf-8")
    return str(path)


def _workspace_artifacts(
    tmp_path: Path,
    *,
    index_history_row: str,
    journal_text: str,
) -> tuple[str, str]:
    workspace = tmp_path / ".trellis" / "workspace" / "sdelmas"
    workspace.mkdir(parents=True)
    index_path = workspace / "index.md"
    journal_path = workspace / "journal-1.md"
    index_path.write_text(
        "\n".join(
            [
                "# Workspace Index - sdelmas",
                "",
                "## Session History",
                "",
                "| # | Date | Title | Commits | Branch |",
                "|---|------|-------|---------|--------|",
                index_history_row,
                "",
            ]
        ),
        encoding="utf-8",
    )
    journal_path.write_text(journal_text, encoding="utf-8")
    return str(index_path), str(journal_path)


def test_clean_artifact_exits_zero(tmp_path: Path) -> None:
    result = _run(_artifact(tmp_path, "### Main Changes\n\n- Added checks.\n"))
    assert result.returncode == 0, result.stderr


def test_add_details_placeholder_exits_one(tmp_path: Path) -> None:
    result = _run(_artifact(tmp_path, "### Main Changes\n\n(Add details)\n"))
    assert result.returncode == 1
    assert "Add details" in result.stderr


def test_add_test_results_placeholder_exits_one(tmp_path: Path) -> None:
    result = _run(_artifact(tmp_path, "- [OK] (Add test results)\n"))
    assert result.returncode == 1
    assert "Add test results" in result.stderr


def test_fill_markers_exit_one(tmp_path: Path) -> None:
    result = _run(_artifact(tmp_path, "TODO: fill this in\nTo be filled later\n"))
    assert result.returncode == 1
    assert "TODO" in result.stderr
    assert "To be filled" in result.stderr


def test_allow_marker_exempts_line(tmp_path: Path) -> None:
    path = _artifact(
        tmp_path,
        "(Add details)  # trellis-placeholder-lint: allow\n",
    )
    result = _run(path)
    assert result.returncode == 0, result.stderr


def test_completed_journal_placeholder_exits_one(tmp_path: Path) -> None:
    index_path, journal_path = _workspace_artifacts(
        tmp_path,
        index_history_row=(
            "| 1 | 2026-06-27 | Finish thing | `abc1234` | `codex/example` |"
        ),
        journal_text="""# Journal - sdelmas

## Session 1: Finish thing

### Main Changes

(Add details)

### Git Commits

| Hash | Message |
|------|---------|
| `abc1234` | (see git log) |

### Testing

- [OK] smoke

### Status

[OK] **Completed**
""",
    )
    result = _run(index_path, journal_path)
    assert result.returncode == 1
    assert "completed journal session 1" in result.stderr
    assert "Add details" in result.stderr


def test_in_progress_journal_placeholder_uses_generic_guard(tmp_path: Path) -> None:
    index_path, journal_path = _workspace_artifacts(
        tmp_path,
        index_history_row=(
            "| 1 | 2026-06-27 | Finish thing | `abc1234` | `codex/example` |"
        ),
        journal_text="""# Journal - sdelmas

## Session 1: Finish thing

### Main Changes

(Add details)

### Git Commits

| Hash | Message |
|------|---------|
| `abc1234` | (see git log) |

### Status

In progress
""",
    )
    result = _run(index_path, journal_path)
    assert result.returncode == 1
    assert "template main-changes placeholder" in result.stderr
    assert "completed journal session" not in result.stderr


def test_workspace_index_and_journal_commit_mismatch_exits_one(tmp_path: Path) -> None:
    index_path, journal_path = _workspace_artifacts(
        tmp_path,
        index_history_row=(
            "| 1 | 2026-06-27 | Finish thing | `abc1234`, `def5678` | `codex/example` |"
        ),
        journal_text="""# Journal - sdelmas

## Session 1: Finish thing

### Main Changes

- Finished the task.

### Git Commits

| Hash | Message |
|------|---------|
| `abc1234` | (see git log) |

### Testing

- [OK] smoke

### Status

[OK] **Completed**
""",
    )
    result = _run(index_path, journal_path)
    assert result.returncode == 1
    assert "journal/index commit list mismatch" in result.stderr
    assert "def5678" in result.stderr


def test_duplicate_journal_session_exits_one(tmp_path: Path) -> None:
    index_path, journal_path = _workspace_artifacts(
        tmp_path,
        index_history_row=(
            "| 1 | 2026-06-27 | Finish thing | `abc1234` | `codex/example` |"
        ),
        journal_text="""# Journal - sdelmas

## Session 1: Finish thing

### Main Changes

- Finished the task.

### Git Commits

| Hash | Message |
|------|---------|
| `abc1234` | (see git log) |

### Testing

- [OK] smoke

### Status

[OK] **Completed**
""",
    )
    duplicate_journal_path = Path(journal_path).with_name("journal-2.md")
    duplicate_journal_path.write_text(
        """# Journal - sdelmas

## Session 1: Duplicate thing

### Main Changes

- Accidentally duplicated the session number.

### Git Commits

| Hash | Message |
|------|---------|
| `def5678` | (see git log) |

### Testing

- [OK] smoke

### Status

[OK] **Completed**
""",
        encoding="utf-8",
    )

    result = _run(index_path, journal_path, str(duplicate_journal_path))
    assert result.returncode == 1
    assert "duplicate journal session 1" in result.stderr
    assert "journal-1.md" in result.stderr
    assert "journal-2.md" in result.stderr


def test_duplicate_journal_session_in_same_file_exits_one(tmp_path: Path) -> None:
    index_path, journal_path = _workspace_artifacts(
        tmp_path,
        index_history_row=(
            "| 1 | 2026-06-27 | Finish thing | `abc1234` | `codex/example` |"
        ),
        journal_text="""# Journal - sdelmas

## Session 1: Finish thing

### Main Changes

- Finished the task.

### Git Commits

| Hash | Message |
|------|---------|
| `abc1234` | (see git log) |

### Testing

- [OK] smoke

### Status

[OK] **Completed**

## Session 1: Duplicate thing

### Main Changes

- Accidentally duplicated the session number.

### Git Commits

| Hash | Message |
|------|---------|
| `def5678` | (see git log) |

### Testing

- [OK] smoke

### Status

[OK] **Completed**
""",
    )

    result = _run(index_path, journal_path)

    assert result.returncode == 1
    assert "duplicate journal session 1" in result.stderr
    assert "journal-1.md" in result.stderr


def test_duplicate_index_session_exits_one(tmp_path: Path) -> None:
    index_path, journal_path = _workspace_artifacts(
        tmp_path,
        index_history_row=(
            "| 1 | 2026-06-27 | Finish thing | `abc1234` | `codex/example` |"
        ),
        journal_text="""# Journal - sdelmas

## Session 1: Finish thing

### Main Changes

- Finished the task.

### Git Commits

| Hash | Message |
|------|---------|
| `abc1234` | (see git log) |

### Testing

- [OK] smoke

### Status

[OK] **Completed**
""",
    )
    index = Path(index_path)
    index.write_text(
        index.read_text(encoding="utf-8").replace(
            "| 1 | 2026-06-27 | Finish thing | `abc1234` | `codex/example` |",
            "\n".join(
                [
                    "| 1 | 2026-06-27 | Finish thing | `abc1234` | `codex/example` |",
                    "| 1 | 2026-06-28 | Duplicate thing | `def5678` | `codex/example` |",
                ]
            ),
        ),
        encoding="utf-8",
    )

    result = _run(index_path, journal_path)

    assert result.returncode == 1
    assert "duplicate index session 1" in result.stderr
    assert "first definition" in result.stderr
    assert "index.md" in result.stderr


def test_index_session_missing_from_journals_exits_one(tmp_path: Path) -> None:
    index_path, journal_path = _workspace_artifacts(
        tmp_path,
        index_history_row=(
            "| 1 | 2026-06-27 | Finish thing | `abc1234` | `codex/example` |"
        ),
        journal_text="""# Journal - sdelmas

## Session 1: Finish thing

### Main Changes

- Finished the task.

### Git Commits

| Hash | Message |
|------|---------|
| `abc1234` | (see git log) |

### Testing

- [OK] smoke

### Status

[OK] **Completed**
""",
    )
    index = Path(index_path)
    index.write_text(
        index.read_text(encoding="utf-8").replace(
            "| 1 | 2026-06-27 | Finish thing | `abc1234` | `codex/example` |",
            "\n".join(
                [
                    "| 1 | 2026-06-27 | Finish thing | `abc1234` | `codex/example` |",
                    "| 2 | 2026-06-28 | Index only | `def5678` | `codex/example` |",
                ]
            ),
        ),
        encoding="utf-8",
    )

    result = _run(index_path, journal_path)

    assert result.returncode == 1
    assert "session 2 is missing from workspace journals" in result.stderr
    assert "index.md" in result.stderr


def test_index_without_journal_input_exits_one(tmp_path: Path) -> None:
    index_path, _journal_path = _workspace_artifacts(
        tmp_path,
        index_history_row=(
            "| 1 | 2026-06-27 | Finish thing | `abc1234` | `codex/example` |"
        ),
        journal_text="""# Journal - sdelmas

## Session 1: Finish thing

### Main Changes

- Finished the task.

### Git Commits

| Hash | Message |
|------|---------|
| `abc1234` | (see git log) |

### Testing

- [OK] smoke

### Status

[OK] **Completed**
""",
    )

    result = _run(index_path)

    assert result.returncode == 1
    assert "workspace journal files are missing from input" in result.stderr
    assert "index.md" in result.stderr


def test_unpassed_journal_file_is_not_included_in_consistency_check(
    tmp_path: Path,
) -> None:
    index_path, journal_path = _workspace_artifacts(
        tmp_path,
        index_history_row=(
            "| 1 | 2026-06-27 | Finish thing | `abc1234` | `codex/example` |"
        ),
        journal_text="""# Journal - sdelmas

## Session 1: Finish thing

### Main Changes

- Finished the task.

### Git Commits

| Hash | Message |
|------|---------|
| `abc1234` | (see git log) |

### Testing

- [OK] smoke

### Status

[OK] **Completed**
""",
    )
    scratch_journal_path = Path(journal_path).with_name("journal-2.md")
    scratch_journal_path.write_text(
        """# Journal - sdelmas

## Session 1: Local scratch duplicate

### Git Commits

| Hash | Message |
|------|---------|
| `def5678` | (see git log) |
""",
        encoding="utf-8",
    )

    result = _run(index_path, journal_path)

    assert result.returncode == 0, result.stderr


def test_workspace_index_root_file_is_not_treated_as_developer_workspace(
    tmp_path: Path,
) -> None:
    index_path = tmp_path / ".trellis" / "workspace" / "index.md"
    index_path.parent.mkdir(parents=True)
    index_path.write_text("# Workspace Index\n", encoding="utf-8")

    result = _run(str(index_path))

    assert result.returncode == 0, result.stderr


def test_workspace_index_and_journal_commit_match_exits_zero(tmp_path: Path) -> None:
    index_path, journal_path = _workspace_artifacts(
        tmp_path,
        index_history_row=(
            "| 1 | 2026-06-27 | Finish thing | `abc1234`, `def5678` | `codex/example` |"
        ),
        journal_text="""# Journal - sdelmas

## Session 1: Finish thing

### Main Changes

- Finished the task.

### Git Commits

| Hash | Message |
|------|---------|
| `abc1234` | (see git log) |
| `def5678` | (see git log) |

### Testing

- [OK] smoke

### Status

[OK] **Completed**
""",
    )
    result = _run(index_path, journal_path)
    assert result.returncode == 0, result.stderr


def test_no_args_exits_two() -> None:
    result = _run()
    assert result.returncode == 2


def test_missing_path_exits_two(tmp_path: Path) -> None:
    result = _run(str(tmp_path / "missing.md"))
    assert result.returncode == 2


def test_non_utf8_file_exits_two(tmp_path: Path) -> None:
    path = tmp_path / "bad.md"
    path.write_bytes(b"\xff\xfe")
    result = _run(str(path))
    assert result.returncode == 2


def test_live_trellis_artifacts_clean() -> None:
    roots = [REPO_ROOT / ".trellis" / "workspace", REPO_ROOT / ".trellis" / "tasks"]
    suffixes = {".md", ".json", ".jsonl", ".yaml", ".yml", ".toml"}
    files = sorted(
        str(path)
        for root in roots
        for path in root.rglob("*")
        if path.is_file() and path.suffix in suffixes
    )
    assert files, "expected Trellis artifacts to guard"
    result = _run(*files)
    assert result.returncode == 0, result.stderr
