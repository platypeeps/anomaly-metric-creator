#!/usr/bin/env python3
"""Synchronize approved repo-local agent skills across platform roots."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = Path(".agents/skills")
PLATFORM_ROOTS = (
    Path(".agents/skills"),
    Path(".claude/skills"),
    Path(".codex/skills"),
    Path(".gemini/skills"),
    Path(".github/skills"),
    Path(".opencode/skills"),
)
DEFAULT_SKILLS = ("security-best-practices", "amc-server-compatibility")
IGNORED_NAMES = {"__pycache__", ".DS_Store"}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Install or update approved skills from .agents/skills in every "
            "supported repo-local agent skill directory."
        )
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
        help="Repository root to update. Defaults to this script's checkout.",
    )
    parser.add_argument(
        "--skill",
        action="append",
        dest="skills",
        help="Skill to synchronize. Repeat to select multiple skills.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify every platform copy without changing files.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned changes without changing files.",
    )
    return parser.parse_args(argv)


def _iter_files(root: Path):
    for path in sorted(root.rglob("*")):
        if any(part in IGNORED_NAMES for part in path.parts):
            continue
        if path.is_file():
            yield path


def _reject_symlinks(root: Path) -> None:
    if root.is_symlink():
        raise ValueError(f"refusing to use symlinked skill directory: {root}")
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"refusing to use skill directory containing symlink: {path}")


def _normalized_bytes(path: Path) -> bytes:
    raw = path.read_bytes()
    if b"\0" in raw:
        return raw
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw
    normalized = "\n".join(
        line.rstrip(" \t")
        for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    )
    return normalized.encode("utf-8")


def _directories_match(source: Path, target: Path) -> bool:
    if not target.is_dir() or target.is_symlink():
        return False
    source_files = {path.relative_to(source) for path in _iter_files(source)}
    target_files = {path.relative_to(target) for path in _iter_files(target)}
    return source_files == target_files and all(
        _normalized_bytes(source / relative) == (target / relative).read_bytes()
        for relative in source_files
    )


def _normalize_text_files(root: Path) -> None:
    for path in _iter_files(root):
        normalized = _normalized_bytes(path)
        if normalized != path.read_bytes():
            path.write_bytes(normalized)


def sync_skill(
    repo_root: Path,
    skill: str,
    *,
    check: bool = False,
    dry_run: bool = False,
) -> list[tuple[Path, str]]:
    source = repo_root / SOURCE_ROOT / skill
    if not source.is_dir() or not (source / "SKILL.md").is_file():
        raise FileNotFoundError(f"canonical skill source is missing SKILL.md: {source}")
    _reject_symlinks(source)

    results: list[tuple[Path, str]] = []
    for platform_root in PLATFORM_ROOTS:
        target = repo_root / platform_root / skill
        if target == source:
            results.append((target, "source"))
            continue
        if target.is_symlink():
            raise ValueError(f"refusing to replace symlinked skill target: {target}")
        if _directories_match(source, target):
            results.append((target, "up-to-date"))
            continue

        status = "missing" if not target.exists() else "out-of-date"
        if check:
            results.append((target, status))
            continue
        if dry_run:
            results.append((target, f"would-{'install' if status == 'missing' else 'update'}"))
            continue
        if target.exists() and not target.is_dir():
            raise ValueError(f"skill target exists but is not a directory: {target}")

        target.parent.mkdir(parents=True, exist_ok=True)
        temp_target = target.parent / f".{target.name}.tmp-{os.getpid()}"
        if temp_target.exists():
            shutil.rmtree(temp_target)
        shutil.copytree(
            source,
            temp_target,
            ignore=shutil.ignore_patterns(*IGNORED_NAMES),
        )
        _normalize_text_files(temp_target)
        if target.exists():
            shutil.rmtree(target)
        temp_target.rename(target)
        results.append((target, "installed" if status == "missing" else "updated"))
    return results


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    repo_root = args.repo_root.resolve(strict=False)
    skills = tuple(dict.fromkeys(args.skills or DEFAULT_SKILLS))
    check_failed = False

    try:
        for skill in skills:
            if not skill or "/" in skill or "\\" in skill or skill in {".", ".."}:
                raise ValueError(f"invalid skill name: {skill!r}")
            for target, status in sync_skill(
                repo_root,
                skill,
                check=args.check,
                dry_run=args.dry_run,
            ):
                print(f"{status}: {target.relative_to(repo_root)}")
                check_failed = check_failed or status in {"missing", "out-of-date"}
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 1 if check_failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
