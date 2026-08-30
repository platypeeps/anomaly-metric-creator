"""Regression coverage for the repo-local agent skill sync command."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "sync-agent-skills.py"
# A fixture name, seeded into a tmp_path tree. It is deliberately not the
# name of a real skill: a roster naming a skill that had stopped existing
# is what left `sync-agent-skills.py` aborting on every run.
SKILL = "fixture-skill"
REPO_SKILL = "amc-server-compatibility"


def _seed_source(
    repo_root: Path,
    body: str = "source",
    *,
    skill: str = SKILL,
) -> Path:
    source = repo_root / ".agents" / "skills" / skill
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(
        f"---\nname: {skill}\ndescription: test\n---\n\n# {body}\n",
        encoding="utf-8",
    )
    (source / "references").mkdir()
    (source / "references" / "python.md").write_text("guidance\n", encoding="utf-8")
    return source


def _run(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--repo-root", str(repo_root), *args],
        check=False,
        capture_output=True,
        text=True,
    )


def test_default_sync_installs_all_six_platform_roots(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    _seed_source(repo_root)
    _seed_source(repo_root, skill=REPO_SKILL)

    result = _run(repo_root)

    assert result.returncode == 0, result.stderr
    for root in (".agents", ".claude", ".codex", ".gemini", ".github", ".opencode"):
        for skill in (SKILL, REPO_SKILL):
            assert (repo_root / root / "skills" / skill / "SKILL.md").is_file()


def test_check_reports_a_missing_claude_copy(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    _seed_source(repo_root)

    result = _run(repo_root, "--skill", SKILL, "--check")

    assert result.returncode == 1
    assert f"missing: .claude/skills/{SKILL}" in result.stdout


def test_sync_replaces_stale_files_and_then_passes_check(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    _seed_source(repo_root, body="current")
    stale = repo_root / ".claude" / "skills" / SKILL
    stale.mkdir(parents=True)
    (stale / "SKILL.md").write_text("stale\n", encoding="utf-8")
    (stale / "remove-me.txt").write_text("stale\n", encoding="utf-8")

    update = _run(repo_root, "--skill", SKILL)
    check = _run(repo_root, "--skill", SKILL, "--check")

    assert update.returncode == 0, update.stderr
    assert f"updated: .claude/skills/{SKILL}" in update.stdout
    assert not (stale / "remove-me.txt").exists()
    assert check.returncode == 0, check.stdout + check.stderr


def test_dry_run_does_not_create_claude_root(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    _seed_source(repo_root)

    result = _run(repo_root, "--skill", SKILL, "--dry-run")

    assert result.returncode == 0, result.stderr
    assert f"would-install: .claude/skills/{SKILL}" in result.stdout
    assert not (repo_root / ".claude").exists()
