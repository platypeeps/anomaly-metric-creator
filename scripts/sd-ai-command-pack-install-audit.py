#!/usr/bin/env python3
"""Audit an installed sd-ai-command-pack footprint in a target repository."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import stat
import subprocess
from collections.abc import Iterable
from pathlib import Path, PurePosixPath, PureWindowsPath


INSTALLED_TARGETS_FILE = Path(".sd-ai-command-pack/installed-targets.txt")
PROVENANCE_FILE = Path(".sd-ai-command-pack/provenance.json")

# Files unique to the sd-ai-command-pack source checkout. A consumer repo never
# has all three (it receives shipped scripts, but not the installer, manifest, or
# template tree), so this is a safe signal that there is nothing installed to audit.
SOURCE_REPO_MARKERS = (
    Path("install.py"),
    Path("manifest.json"),
    Path("templates"),
)

PACK_FILE_PATTERNS = [
    ".agents/skills/sd-*/*",
    ".claude/commands/sd/*",
    ".cursor/commands/sd-*",
    ".gemini/commands/sd/*",
    ".github/prompts/sd-*.prompt.md",
    ".opencode/commands/sd-*.md",
    ".gito/config.toml",
    ".gito/sd-ai-command-pack.env",
    ".prism/rules.json",
    ".sd-ai-command-pack/*",
    "docs/SD_AI_COMMAND_PACK.md",
    "scripts/sd-ai-command-pack-*",
]

LOCAL_ALLOWED_PACK_FILES = {
    ".sd-ai-command-pack/pr-body-scope.json",
    ".sd-ai-command-pack/review-preflight.json",
}

LEGACY_PACK_PATHS = {
    ".agents/skills/sd-refresh-specs": "use .agents/skills/sd-update-spec",
    ".agents/skills/trellis-full-check": "use .agents/skills/sd-full-check",
    ".agents/skills/trellis-housekeeping": "use .agents/skills/sd-housekeeping",
    ".agents/skills/trellis-review-pr": "use .agents/skills/sd-review-pr",
    ".claude/commands/sd/refresh-specs.md": "use .claude/commands/sd/update-spec.md",
    ".cursor/commands/sd-refresh-specs.md": "use .cursor/commands/sd-update-spec.md",
    ".gemini/commands/sd/refresh-specs.toml": "use .gemini/commands/sd/update-spec.toml",
    ".github/prompts/sd-refresh-specs.prompt.md": "use .github/prompts/sd-update-spec.prompt.md",
    ".opencode/commands/sd-refresh-specs.md": "use .opencode/commands/sd-update-spec.md",
    "scripts/trellis-full-check.sh": "use scripts/sd-ai-command-pack-full-check.sh",
    "scripts/trellis-housekeeping.sh": "use scripts/sd-ai-command-pack-housekeeping.sh",
}

LEGACY_PACK_REFERENCES = {
    "scripts/trellis-full-check.sh": "scripts/sd-ai-command-pack-full-check.sh",
    "scripts/trellis-housekeeping.sh": "scripts/sd-ai-command-pack-housekeeping.sh",
    "trellis-full-check": "sd-full-check",
    "trellis-housekeeping": "sd-housekeeping",
    "trellis-review-pr": "sd-review-pr",
    "sd-refresh-specs": "sd-update-spec",
    "TRELLIS_FULL_CHECK": "SD_AI_COMMAND_PACK_FULL_CHECK",
    "TRELLIS_HOUSEKEEPING": "SD_AI_COMMAND_PACK_HOUSEKEEPING",
}
LEGACY_REFERENCE_BOUNDARY = r"[A-Za-z0-9_.-]"
LEGACY_PACK_REFERENCE_PATTERNS = {
    needle: re.compile(
        rf"(?<!{LEGACY_REFERENCE_BOUNDARY}){re.escape(needle)}(?!{LEGACY_REFERENCE_BOUNDARY})"
    )
    for needle in LEGACY_PACK_REFERENCES
}

REFERENCE_SCAN_BASES = (
    "AGENTS.md",
    "CLAUDE.md",
    "README.md",
    ".agents",
    ".claude",
    ".cursor",
    ".gemini",
    ".github",
    ".opencode",
    ".trellis/spec",
    "docs",
    "scripts",
    "tests",
    "tools",
)

REFERENCE_SCAN_EXCLUDED_PARTS = {
    ".git",
    ".obsidian-kb",
    ".trellis/workspace",
    "__pycache__",
    "node_modules",
}

MAX_REFERENCE_SCAN_BYTES = 1_000_000


def is_disabled(value: str | None) -> bool:
    return (value or "").lower() in {"0", "false", "no", "skip", "none"}


def is_pack_source_checkout(root: Path) -> bool:
    return all((root / marker).exists() for marker in SOURCE_REPO_MARKERS)


def is_unsafe_installed_target(path_text: str) -> bool:
    posix_path = PurePosixPath(path_text.replace("\\", "/"))
    windows_path = PureWindowsPath(path_text)
    return (
        posix_path.is_absolute()
        or bool(windows_path.drive)
        or bool(windows_path.root)
        or ".." in posix_path.parts
        or ".." in windows_path.parts
    )


def load_installed_targets(root: Path) -> tuple[set[str], list[str]]:
    targets_file = root / INSTALLED_TARGETS_FILE
    try:
        raw_text = targets_file.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return set(), [f"{INSTALLED_TARGETS_FILE} is missing"]
    except OSError as exc:
        # Path.exists() would swallow (or on some Python versions raise)
        # permission errors; report them instead of crashing or misreading
        # an unreadable receipt as absent.
        return set(), [f"{INSTALLED_TARGETS_FILE} cannot be read: {exc}"]

    targets: set[str] = set()
    failures: list[str] = []
    for line_number, raw_line in enumerate(
        raw_text.splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if is_unsafe_installed_target(line):
            failures.append(
                f"{INSTALLED_TARGETS_FILE}:{line_number} contains unsafe target {line!r}"
            )
            continue
        # Receipts are generated with POSIX separators; tolerate hand-edited
        # Windows-style relative entries so Path()/check-ignore behave the
        # same on every platform.
        targets.add(line.replace("\\", "/"))

    if not targets:
        failures.append(f"{INSTALLED_TARGETS_FILE} has no installed targets")

    return targets, failures


def path_exists(root: Path, relative_path: Path) -> bool:
    # lstat-based: Path.exists() swallows OSErrors on some Python versions
    # and raises on others (observed crashing on 3.9 under an unreadable
    # parent directory); "cannot be inspected" counts as absent here and
    # the provenance audit reports the inspection failure precisely.
    path = root / relative_path
    try:
        os.lstat(path)
    except OSError:
        return False
    return True


def matches_pack_file(relative_path: str) -> bool:
    return any(fnmatch.fnmatchcase(relative_path, pattern) for pattern in PACK_FILE_PATTERNS)


def collect_pack_like_files(root: Path) -> list[str]:
    """Return installed-pack-shaped files that exist under known pack locations."""
    bases = [
        ".agents/skills",
        ".claude/commands",
        ".cursor/commands",
        ".gemini/commands",
        ".github/prompts",
        ".gito",
        ".opencode/commands",
        ".prism",
        ".sd-ai-command-pack",
        "docs",
        "scripts",
    ]

    pack_like: list[str] = []
    for base in bases:
        base_path = root / base
        if not base_path.exists():
            continue
        for path in base_path.rglob("*"):
            if path.is_file() or path.is_symlink():
                relative_path = path.relative_to(root).as_posix()
                if matches_pack_file(relative_path):
                    pack_like.append(relative_path)

    return sorted(set(pack_like))


def is_gitignored(root: Path, relative_path: str) -> bool:
    """True when git confirms the path is ignored; False otherwise.

    Missing git, a non-repo root, and git errors all return False, so the
    caller keeps the fail-closed error behavior for those cases.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "check-ignore", "-q", "--", relative_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return False
    return result.returncode == 0


def audit_structural_state(root: Path, targets: set[str]) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    warnings: list[str] = []

    for target in sorted(targets):
        if path_exists(root, Path(target)):
            continue
        # Platform adapters may be recorded by a checkout that has them
        # while being gitignored (e.g. repos ignoring .claude/): absence
        # here is a local-only gap, not receipt drift.
        if is_gitignored(root, target):
            warnings.append(
                "installed target is gitignored and absent in this "
                f"checkout: {target}; re-run the pack installer here to "
                "materialize local-only adapters"
            )
        else:
            failures.append(f"installed target is missing: {target}")

    allowed = set(targets) | LOCAL_ALLOWED_PACK_FILES
    for relative_path in collect_pack_like_files(root):
        if relative_path in allowed:
            continue
        # Repos may deliberately keep gitignored local-only adapters out of
        # the tracked receipt (the exclude-and-warn policy); tolerate that.
        if is_gitignored(root, relative_path):
            warnings.append(
                "local-only pack-like file is not recorded in installed "
                f"targets: {relative_path} (gitignored; repo receipt policy "
                "may exclude local-only adapters)"
            )
        else:
            failures.append(
                "pack-like file is not listed in installed targets: "
                f"{relative_path}"
            )

    return failures, warnings


def audit_provenance(root: Path) -> tuple[list[str], str | None]:
    """Verify recorded pack content hashes when provenance is present."""
    provenance_path = root / PROVENANCE_FILE
    try:
        mode = os.lstat(provenance_path).st_mode
    except FileNotFoundError:
        return [], None
    except OSError as exc:
        # exists()/is_file() swallow OSErrors as False, which would let a
        # permission game silently disable verification; fail instead.
        return [f"{PROVENANCE_FILE} cannot be inspected: {exc}"], None
    if not stat.S_ISREG(mode):
        # A symlinked or non-regular provenance file would let tampering
        # redirect or disable verification; only a regular file counts
        # (lstat does not follow symlinks).
        return [f"{PROVENANCE_FILE} must be a regular file"], None

    try:
        payload = json.loads(
            provenance_path.read_text(encoding="utf-8", errors="strict")
        )
    except (OSError, UnicodeError, ValueError) as exc:
        return [f"{PROVENANCE_FILE} is unreadable or malformed: {exc}"], None

    files = payload.get("files") if isinstance(payload, dict) else None
    raw_version = payload.get("version") if isinstance(payload, dict) else None
    version = (
        raw_version
        if isinstance(raw_version, str) and raw_version.strip()
        else "unknown"
    )
    if not isinstance(files, dict):
        return [f"{PROVENANCE_FILE} has no files map"], None
    if not files:
        return [f"{PROVENANCE_FILE} has an empty files map"], None

    failures: list[str] = []
    root_real = os.path.realpath(root)
    for raw_target, expected in sorted(files.items()):
        if not isinstance(raw_target, str) or not isinstance(expected, str):
            failures.append(f"{PROVENANCE_FILE} has a malformed entry: {raw_target!r}")
            continue
        target = raw_target.replace("\\", "/")
        if is_unsafe_installed_target(target):
            failures.append(f"{PROVENANCE_FILE} contains unsafe target {raw_target!r}")
            continue
        path = root / target
        # Per-target lstat mirrors the provenance-file gate: missing,
        # symlink, non-regular, and cannot-be-inspected are distinguished
        # without exists()/is_file() OSError ambiguity. It runs before the
        # escape check so a symlink target keeps its "not a regular file"
        # classification wherever it points.
        try:
            target_mode = os.lstat(path).st_mode
        except FileNotFoundError:
            # Local-only adapters may be legitimately absent (gitignored);
            # anything else vouched-but-gone is tampering even when the
            # receipt no longer lists it.
            if not is_gitignored(root, target):
                failures.append(f"vouched target is missing: {target}")
            continue
        except OSError as exc:
            failures.append(
                f"vouched target cannot be inspected: {target}: {exc}"
            )
            continue
        if not stat.S_ISREG(target_mode):
            # Provenance vouches plain regular files; a symlink (even to a
            # matching file), directory, or other node at a vouched path is
            # tampering, not absence.
            failures.append(f"vouched target is not a regular file: {target}")
            continue
        # Symlinked parent directories could route the hash check outside
        # the repository; fail closed when the real path escapes root.
        # commonpath handles filesystem-root repos and raises on
        # mixed-drive comparisons, which also fail closed.
        real = os.path.realpath(path)
        try:
            inside = os.path.commonpath([root_real, real]) == root_real
        except ValueError:
            inside = False
        if not inside:
            failures.append(
                f"vouched target escapes the repository root: {target}"
            )
            continue
        try:
            content = path.read_bytes()
        except OSError as exc:
            failures.append(f"vouched target is unreadable: {target}: {exc}")
            continue
        digest = "sha256:" + hashlib.sha256(content).hexdigest()
        if digest != expected:
            failures.append(
                f"installed target drifted from pack {version} content: "
                f"{target} (re-run the pack installer or review the local edit)"
            )
    return failures, version


def _is_excluded_scan_path(relative_path: Path) -> bool:
    path_text = relative_path.as_posix()
    for excluded in REFERENCE_SCAN_EXCLUDED_PARTS:
        if "/" in excluded:
            if path_text == excluded or path_text.startswith(f"{excluded}/"):
                return True
            continue
        if excluded in relative_path.parts:
            return True
    return False


def _iter_reference_scan_candidates(root: Path) -> Iterable[Path]:
    """Yield files from configured reference-scan roots before filtering."""
    for base in REFERENCE_SCAN_BASES:
        base_path = root / base
        if not base_path.exists() or base_path.is_symlink():
            continue
        if base_path.is_file():
            yield base_path.relative_to(root)
            continue
        for path in base_path.rglob("*"):
            if path.is_symlink() or not path.is_file():
                continue
            yield path.relative_to(root)


def _iter_reference_scan_files(root: Path, skipped_paths: set[str]) -> list[Path]:
    """Return non-pack, non-skipped text candidates for legacy-reference scans."""
    files: list[Path] = []
    for relative_path in _iter_reference_scan_candidates(root):
        relative_text = relative_path.as_posix()
        if (
            relative_text in skipped_paths
            or matches_pack_file(relative_text)
            or _is_excluded_scan_path(relative_path)
        ):
            continue
        files.append(relative_path)
    return sorted(set(files), key=lambda path: path.as_posix())


def audit_migration_advisories(root: Path, targets: set[str]) -> list[str]:
    """Report legacy pack paths and whole-token references that remain."""
    warnings: list[str] = []

    for relative_path, replacement in LEGACY_PACK_PATHS.items():
        if path_exists(root, Path(relative_path)):
            warnings.append(
                f"legacy pack target remains: {relative_path}; {replacement}"
            )

    for relative_path in _iter_reference_scan_files(root, targets):
        path = root / relative_path
        try:
            if path.stat().st_size > MAX_REFERENCE_SCAN_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for needle, replacement in LEGACY_PACK_REFERENCES.items():
            if LEGACY_PACK_REFERENCE_PATTERNS[needle].search(text):
                warnings.append(
                    "legacy pack reference remains: "
                    f"{relative_path.as_posix()} contains {needle!r}; "
                    f"prefer {replacement}"
                )

    return sorted(set(warnings))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit the installed sd-ai-command-pack footprint."
    )
    parser.add_argument(
        "--repo",
        default=".",
        help="target repository root to audit; defaults to the current directory",
    )
    return parser.parse_args()


def main() -> int:
    if is_disabled(os.environ.get("SD_AI_COMMAND_PACK_INSTALL_AUDIT")):
        print("warning: skipping install audit because SD_AI_COMMAND_PACK_INSTALL_AUDIT is disabled")
        return 0

    args = parse_args()
    root = Path(args.repo).resolve()

    if is_pack_source_checkout(root) and not (root / INSTALLED_TARGETS_FILE).exists():
        print(
            "skipping install audit: running inside the sd-ai-command-pack "
            "source checkout (no installed footprint to audit)"
        )
        return 0

    targets, failures = load_installed_targets(root)
    structural_warnings: list[str] = []
    if targets:
        structural_failures, structural_warnings = audit_structural_state(root, targets)
        failures.extend(structural_failures)
    provenance_failures, provenance_version = audit_provenance(root)
    failures.extend(provenance_failures)
    warnings = [*structural_warnings, *audit_migration_advisories(root, targets)]

    if failures:
        for failure in failures:
            print(f"error: {failure}")
        return 1

    for warning in warnings:
        print(f"warning: {warning}")

    print(f"SD AI command pack install audit passed: {len(targets)} targets checked.")
    if provenance_version is not None:
        print(
            "Installed payload provenance: "
            f"version {provenance_version}; vouched file hashes match."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
