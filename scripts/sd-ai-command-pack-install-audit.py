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
PACK_MANIFEST_FILE = Path(".sd-ai-command-pack/manifest.json")
GIT_TIMEOUT_SECONDS = 60
STABLE_VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")

# Files unique to the sd-ai-command-pack source checkout. A consumer repo never
# has all three (it receives shipped scripts, but not the installer, manifest, or
# template tree), so this is a safe signal that there is nothing installed to audit.
SOURCE_REPO_MARKERS = (
    Path("install.py"),
    Path("manifest.json"),
    Path("templates"),
)

PACK_FILE_PATTERNS = [
    ".agent/skills/sd-*/*",
    ".agent/workflows/sd-*",
    ".agents/skills/sd-*/*",
    ".claude/commands/sd/*",
    ".codebuddy/commands/sd/*",
    ".codebuddy/skills/sd-*/*",
    ".cursor/commands/sd-*",
    ".devin/skills/sd-*/*",
    ".devin/workflows/sd-*",
    ".factory/commands/sd/*",
    ".factory/skills/sd-*/*",
    ".gemini/commands/sd/*",
    ".github/prompts/sd-*.prompt.md",
    ".kilocode/skills/sd-*/*",
    ".kilocode/workflows/sd-*",
    ".kiro/skills/sd-*/*",
    ".opencode/commands/sd-*.md",
    ".pi/prompts/sd-*",
    ".pi/skills/sd-*/*",
    ".qoder/commands/sd-*",
    ".qoder/skills/sd-*/*",
    ".reasonix/skills/sd-*/*",
    ".trae/commands/sd-*",
    ".trae/skills/sd-*/*",
    ".zcode/commands/sd/*",
    ".github/PULL_REQUEST_TEMPLATE.md",
    ".gito/config.toml",
    ".gito/sd-ai-command-pack.env",
    ".prism/rules.json",
    ".prism/rules.schema.json",
    ".sd-ai-command-pack/*",
    "docs/SD_AI_COMMAND_PACK.md",
    "scripts/sd-ai-command-pack-*",
    "scripts/sd_ai_command_pack_lib.py",
    "scripts/sd_ai_command_pack_fleet_lib.py",
]

LOCAL_ALLOWED_PACK_FILES = {
    ".sd-ai-command-pack/pr-body-scope.json",
    ".sd-ai-command-pack/review-preflight.json",
}

SOURCE_ONLY_ALLOWED_PACK_FILES = {
    ".agent/skills/sd-fleet-refresh/SKILL.md",
    ".agent/workflows/sd-fleet-refresh.md",
    ".agents/skills/sd-fleet-refresh/SKILL.md",
    ".claude/commands/sd/fleet-refresh.md",
    ".codebuddy/commands/sd/fleet-refresh.md",
    ".codebuddy/skills/sd-fleet-refresh/SKILL.md",
    ".cursor/commands/sd-fleet-refresh.md",
    ".devin/skills/sd-fleet-refresh/SKILL.md",
    ".devin/workflows/sd-fleet-refresh.md",
    ".factory/commands/sd/fleet-refresh.md",
    ".factory/skills/sd-fleet-refresh/SKILL.md",
    ".gemini/commands/sd/fleet-refresh.toml",
    ".github/prompts/sd-fleet-refresh.prompt.md",
    ".kilocode/skills/sd-fleet-refresh/SKILL.md",
    ".kilocode/workflows/sd-fleet-refresh.md",
    ".kiro/skills/sd-fleet-refresh/SKILL.md",
    ".opencode/commands/sd-fleet-refresh.md",
    ".pi/prompts/sd-fleet-refresh.md",
    ".pi/skills/sd-fleet-refresh/SKILL.md",
    ".qoder/commands/sd-fleet-refresh.md",
    ".qoder/skills/sd-fleet-refresh/SKILL.md",
    ".reasonix/skills/sd-fleet-refresh/SKILL.md",
    ".trae/commands/sd-fleet-refresh.md",
    ".trae/skills/sd-fleet-refresh/SKILL.md",
    ".zcode/commands/sd/fleet-refresh.md",
    "scripts/sd-ai-command-pack-fleet-candidate-check.py",
    "scripts/sd-ai-command-pack-fleet-finding-classify.py",
    "scripts/sd-ai-command-pack-fleet-preflight.py",
    "scripts/sd-ai-command-pack-fleet-review-classify.py",
    "scripts/sd-ai-command-pack-fleet-timing.py",
    "scripts/sd-ai-command-pack-fleet-wave-plan.py",
    "scripts/sd_ai_command_pack_fleet_lib.py",
}

PROVENANCE_NEVER_VOUCHED_TARGETS = {
    ".gitignore",
    ".gito/config.toml",
    ".github/PULL_REQUEST_TEMPLATE.md",
    ".github/copilot-instructions.md",
    ".prism/rules.json",
    INSTALLED_TARGETS_FILE.as_posix(),
    PACK_MANIFEST_FILE.as_posix(),
    PROVENANCE_FILE.as_posix(),
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
    # Pack rename era (trellis-review-pr-pack / sd-command-pack -> sd-ai-command-pack):
    "docs/TRELLIS_REVIEW_PR_PACK.md": "use docs/SD_AI_COMMAND_PACK.md",
    **{
        f".opencode/commands/sd/{command}.md": (
            f"use .opencode/commands/sd-{command}.md"
        )
        for command in (
            "start",
            "continue",
            "finish-work",
            "create-pr",
            "full-check",
            "housekeeping",
            "review-learnings",
            "review-local",
            "review-local-all",
            "review-pr",
            "update-spec",
        )
    },
    **{
        f"scripts/sd-command-pack-{name}": (
            f"use scripts/sd-ai-command-pack-{name}"
        )
        for name in (
            "full-check.sh",
            "housekeeping.sh",
            "install-audit.py",
            "pr-body-scope.py",
            "record-session.py",
            "review-learnings.py",
            "review-local.sh",
            "review-preflight.mjs",
            "review-scope.sh",
            "shell-lib.sh",
            "update-spec-kb.py",
            "work-loop.py",
        )
    },
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
    # Pack rename era: needles are full tokens because the boundary class
    # treats "-" and "." as word characters.
    "TRELLIS_REVIEW_PR_PACK.md": "SD_AI_COMMAND_PACK.md",
    **{
        f"sd-command-pack-{name}": f"sd-ai-command-pack-{name}"
        for name in (
            "full-check.sh",
            "housekeeping.sh",
            "install-audit.py",
            "pr-body-scope.py",
            "record-session.py",
            "review-learnings.py",
            "review-local.sh",
            "review-preflight.mjs",
            "review-scope.sh",
            "update-spec-kb.py",
        )
    },
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
    ".agent",
    ".agents",
    ".claude",
    ".codebuddy",
    ".codex",
    ".cursor",
    ".devin",
    ".factory",
    ".gemini",
    ".github",
    ".kilocode",
    ".kiro",
    ".opencode",
    ".pi",
    ".qoder",
    ".reasonix",
    ".trae",
    ".zcode",
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
REFERENCE_SCAN_EXCLUDED_NAMES = {"repomix-map.md"}

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


def load_pack_manifest(root: Path) -> tuple[dict | None, list[str]]:
    manifest_path = root / PACK_MANIFEST_FILE
    try:
        raw_text = manifest_path.read_text(encoding="utf-8", errors="strict")
    except FileNotFoundError:
        return None, []
    except OSError as exc:
        return None, [f"{PACK_MANIFEST_FILE} cannot be read: {exc}"]
    except UnicodeError as exc:
        return None, [f"{PACK_MANIFEST_FILE} is not valid UTF-8: {exc}"]

    try:
        payload = json.loads(raw_text)
    except ValueError as exc:
        return None, [f"{PACK_MANIFEST_FILE} is not valid JSON: {exc}"]
    if not isinstance(payload, dict):
        return None, [f"{PACK_MANIFEST_FILE} must be a JSON object"]
    files = payload.get("files")
    if not isinstance(files, list):
        return None, [f"{PACK_MANIFEST_FILE} has no files list"]
    return payload, []


def manifest_file_records(manifest: dict) -> tuple[list[dict[str, str]], list[str]]:
    records: list[dict[str, str]] = []
    failures: list[str] = []
    files = manifest.get("files")
    if not isinstance(files, list):
        return records, [f"{PACK_MANIFEST_FILE} has no files list"]
    for index, item in enumerate(files):
        if not isinstance(item, dict):
            failures.append(f"{PACK_MANIFEST_FILE}: files[{index}] must be an object")
            continue
        raw_platform = item.get("platform")
        raw_target = item.get("target")
        raw_install = item.get("install", "if-anchor-exists")
        if not isinstance(raw_platform, str) or not raw_platform:
            failures.append(
                f"{PACK_MANIFEST_FILE}: files[{index}] has invalid platform"
            )
            continue
        if not isinstance(raw_target, str) or not raw_target:
            failures.append(f"{PACK_MANIFEST_FILE}: files[{index}] has invalid target")
            continue
        target = raw_target.replace("\\", "/")
        if is_unsafe_installed_target(target):
            failures.append(
                f"{PACK_MANIFEST_FILE}: files[{index}] contains unsafe target "
                f"{raw_target!r}"
            )
            continue
        install_mode = raw_install if isinstance(raw_install, str) else ""
        records.append(
            {
                "platform": raw_platform,
                "target": target,
                "install": install_mode or "if-anchor-exists",
            }
        )
    return records, failures


def inferred_platforms_from_targets(
    targets: set[str],
    manifest_records: list[dict[str, str]],
) -> set[str]:
    return {
        record["platform"]
        for record in manifest_records
        if record["platform"] != "shared" and record["target"] in targets
    }


def expected_targets_from_manifest(
    manifest: dict,
    targets: set[str],
    explicit_platforms: Iterable[str],
) -> tuple[set[str], set[str], list[str]]:
    records, failures = manifest_file_records(manifest)
    if failures:
        return set(), set(), failures

    known_platforms = {
        record["platform"]
        for record in records
        if record["platform"] != "shared"
    }
    selected_platforms = inferred_platforms_from_targets(targets, records)
    for platform in explicit_platforms:
        if platform not in known_platforms:
            failures.append(f"unknown expected platform: {platform}")
        else:
            selected_platforms.add(platform)
    if failures:
        return set(), selected_platforms, failures

    expected = {
        record["target"]
        for record in records
        if record["platform"] == "shared"
        or record["install"] in {"always", "if-not-exists"}
        or record["platform"] in selected_platforms
    }
    expected.update(
        {
            INSTALLED_TARGETS_FILE.as_posix(),
            PACK_MANIFEST_FILE.as_posix(),
            PROVENANCE_FILE.as_posix(),
        }
    )
    if ".gitignore" in targets:
        expected.add(".gitignore")
    return expected, selected_platforms, []


def audit_expected_targets(
    root: Path,
    targets: set[str],
    manifest: dict | None,
    *,
    explicit_platforms: Iterable[str],
) -> tuple[list[str], list[str], int | None, set[str]]:
    if manifest is None:
        return (
            [],
            [
                f"{PACK_MANIFEST_FILE} is absent; skipping expected-target "
                "completeness check until the pack is reinstalled or updated"
            ],
            None,
            set(),
        )

    expected, selected_platforms, failures = expected_targets_from_manifest(
        manifest,
        targets,
        explicit_platforms,
    )
    warnings: list[str] = []
    if failures:
        return failures, warnings, None, selected_platforms

    for target in sorted(expected - targets):
        failures.append(f"expected installed target is missing from receipt: {target}")

    target_events: list[tuple[str, str]] = []
    for target in sorted(expected):
        target_state = inspect_target_presence(root, Path(target))
        if target_state == "present":
            continue
        if target_state != "missing":
            target_events.append(
                (
                    "failure",
                    (
                        f"expected installed target cannot be inspected: {target} "
                        f"({target_state})"
                    ),
                )
            )
            continue
        target_events.append(("missing", target))

    ignored_targets = gitignored_paths(
        root,
        (
            target
            for event_type, target in target_events
            if event_type == "missing"
        ),
    )
    for event_type, target in target_events:
        if event_type == "failure":
            failures.append(target)
        elif target in ignored_targets:
            warnings.append(
                "expected installed target is gitignored and absent in this "
                f"checkout: {target}; re-run the pack installer here to "
                "materialize local-only adapters"
            )
        else:
            failures.append(f"expected installed target is missing: {target}")

    return failures, warnings, len(expected), selected_platforms


def inspect_target_presence(root: Path, relative_path: Path) -> str:
    """Return "present", "missing", or the OS error detail for unreadable
    targets, so permission problems are never misreported as missing files."""
    try:
        os.lstat(root / relative_path)
    except FileNotFoundError:
        return "missing"
    except OSError as error:
        # Stable, path-free detail: the failure line already names the
        # relative target, and absolute paths do not belong in shared logs.
        return error.strerror or error.__class__.__name__
    return "present"


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


def pack_scan_bases() -> tuple[str, ...]:
    """Derive walk roots from PACK_FILE_PATTERNS so pattern and scan coverage
    cannot drift apart: each base is a pattern's longest glob-free directory
    prefix."""
    bases: set[str] = set()
    for pattern in PACK_FILE_PATTERNS:
        prefix: list[str] = []
        for part in pattern.split("/")[:-1]:
            if any(character in part for character in "*?["):
                break
            prefix.append(part)
        if prefix:
            bases.add("/".join(prefix))
    return tuple(sorted(bases))


def collect_pack_like_files(root: Path) -> list[str]:
    """Return installed-pack-shaped files that exist under known pack locations."""
    pack_like: list[str] = []
    for base in pack_scan_bases():
        base_path = root / base
        if not base_path.exists():
            continue
        for path in base_path.rglob("*"):
            if path.is_file() or path.is_symlink():
                relative_path = path.relative_to(root).as_posix()
                if matches_pack_file(relative_path):
                    pack_like.append(relative_path)

    return sorted(set(pack_like))


def gitignored_paths(root: Path, relative_paths: Iterable[str]) -> set[str]:
    """Return paths git confirms as ignored, using one check-ignore process.

    Missing git, a non-repo root, and git errors all return an empty set, so
    callers keep the fail-closed error behavior for those cases.
    """
    candidates = sorted(set(relative_paths))
    if not candidates:
        return set()
    input_payload = b"".join(os.fsencode(path) + b"\0" for path in candidates)
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "check-ignore", "--stdin", "-z"],
            input=input_payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    if result.returncode not in {0, 1}:
        return set()
    return {
        os.fsdecode(raw_path)
        for raw_path in result.stdout.split(b"\0")
        if raw_path
    }


def is_gitignored(root: Path, relative_path: str) -> bool:
    """True when git confirms the path is ignored; False otherwise."""
    return relative_path in gitignored_paths(root, [relative_path])


def audit_structural_state(root: Path, targets: set[str]) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    warnings: list[str] = []
    target_events: list[tuple[str, str]] = []

    for target in sorted(targets):
        target_state = inspect_target_presence(root, Path(target))
        if target_state == "present":
            continue
        if target_state != "missing":
            target_events.append(
                (
                    "failure",
                    f"installed target cannot be inspected: {target} ({target_state})",
                )
            )
            continue
        # Platform adapters may be recorded by a checkout that has them
        # while being gitignored (e.g. repos ignoring .claude/): absence
        # here is a local-only gap, not receipt drift.
        target_events.append(("missing-target", target))

    allowed = set(targets) | LOCAL_ALLOWED_PACK_FILES
    if is_pack_source_checkout(root):
        allowed |= SOURCE_ONLY_ALLOWED_PACK_FILES
    for relative_path in collect_pack_like_files(root):
        if relative_path in allowed:
            continue
        # Repos may deliberately keep gitignored local-only adapters out of
        # the tracked receipt (the exclude-and-warn policy); tolerate that.
        target_events.append(("unlisted-pack-like", relative_path))

    ignored_targets = gitignored_paths(
        root,
        (
            target
            for event_type, target in target_events
            if event_type != "failure"
        ),
    )
    for event_type, target in target_events:
        if event_type == "failure":
            failures.append(target)
        elif event_type == "missing-target":
            if target in ignored_targets:
                warnings.append(
                    "installed target is gitignored and absent in this "
                    f"checkout: {target}; re-run the pack installer here to "
                    "materialize local-only adapters"
                )
            else:
                failures.append(f"installed target is missing: {target}")
        elif target in ignored_targets:
            warnings.append(
                "local-only pack-like file is not recorded in installed "
                f"targets: {target} (gitignored; repo receipt policy "
                "may exclude local-only adapters)"
            )
        else:
            failures.append(
                "pack-like file is not listed in installed targets: "
                f"{target}"
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

    provenance_events: list[tuple[str, str]] = []
    root_real = os.path.realpath(root)
    for raw_target, expected in sorted(files.items()):
        if not isinstance(raw_target, str) or not isinstance(expected, str):
            provenance_events.append(
                ("failure", f"{PROVENANCE_FILE} has a malformed entry: {raw_target!r}")
            )
            continue
        target = raw_target.replace("\\", "/")
        if is_unsafe_installed_target(target):
            provenance_events.append(
                ("failure", f"{PROVENANCE_FILE} contains unsafe target {raw_target!r}")
            )
            continue
        if target in PROVENANCE_NEVER_VOUCHED_TARGETS:
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
            provenance_events.append(("missing", target))
            continue
        except OSError as exc:
            provenance_events.append(
                ("failure", f"vouched target cannot be inspected: {target}: {exc}")
            )
            continue
        if not stat.S_ISREG(target_mode):
            # Provenance vouches plain regular files; a symlink (even to a
            # matching file), directory, or other node at a vouched path is
            # tampering, not absence.
            provenance_events.append(
                ("failure", f"vouched target is not a regular file: {target}")
            )
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
            provenance_events.append(
                (
                    "failure",
                    f"vouched target escapes the repository root: {target}",
                )
            )
            continue
        try:
            content = path.read_bytes()
        except OSError as exc:
            provenance_events.append(
                ("failure", f"vouched target is unreadable: {target}: {exc}")
            )
            continue
        digest = "sha256:" + hashlib.sha256(content).hexdigest()
        if digest != expected:
            provenance_events.append(
                (
                    "failure",
                    f"installed target drifted from pack {version} content: "
                    f"{target} (re-run the pack installer or review the local edit)",
                )
            )

    ignored_targets = gitignored_paths(
        root,
        (
            target
            for event_type, target in provenance_events
            if event_type == "missing"
        ),
    )
    failures: list[str] = []
    for event_type, target in provenance_events:
        if event_type == "failure":
            failures.append(target)
        elif target not in ignored_targets:
            failures.append(f"vouched target is missing: {target}")
    return failures, version


def _is_excluded_scan_path(relative_path: Path) -> bool:
    if relative_path.name in REFERENCE_SCAN_EXCLUDED_NAMES:
        return True
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

    for scan_path in _iter_reference_scan_files(root, targets):
        path = root / scan_path
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
                    f"{scan_path.as_posix()} contains {needle!r}; "
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
    parser.add_argument(
        "--expected-platform",
        action="append",
        default=[],
        help=(
            "Require this platform's manifest targets to be installed even if "
            "the current receipt does not already imply the platform. Repeat "
            "for fleet manifest checks."
        ),
    )
    parser.add_argument(
        "--upstream-manifest",
        help=(
            "advisory-only comparison against an upstream manifest.json or "
            "pack checkout directory; unavailable or incomparable versions "
            "do not change the audit exit code"
        ),
    )
    return parser.parse_args()


def version_update_advisory(
    installed_version: str | None,
    reference: str,
) -> str:
    reference_path = Path(reference).expanduser()
    if reference_path.is_dir():
        reference_path = reference_path / "manifest.json"
    try:
        payload = json.loads(
            reference_path.read_text(encoding="utf-8", errors="strict")
        )
    except (OSError, UnicodeError, ValueError) as error:
        return (
            "Pack version update check: could not determine upstream version "
            f"from {reference_path}: {error}"
        )

    upstream_version = payload.get("version") if isinstance(payload, dict) else None
    if not isinstance(installed_version, str) or not installed_version:
        return "Pack version update check: could not determine installed version."
    if not isinstance(upstream_version, str) or not upstream_version:
        return (
            "Pack version update check: could not determine upstream version "
            f"from {reference_path}: missing string version"
        )
    if not STABLE_VERSION_PATTERN.fullmatch(installed_version) or not (
        STABLE_VERSION_PATTERN.fullmatch(upstream_version)
    ):
        return (
            "Pack version update check: could not compare installed "
            f"{installed_version} with upstream {upstream_version}; "
            "expected stable MAJOR.MINOR.PATCH versions."
        )

    installed_key = tuple(int(part) for part in installed_version.split("."))
    upstream_key = tuple(int(part) for part in upstream_version.split("."))
    if installed_key < upstream_key:
        relation = "behind"
    elif installed_key > upstream_key:
        relation = "ahead of"
    else:
        relation = "current with"
    return (
        f"Pack version update check: installed {installed_version} is "
        f"{relation} upstream {upstream_version}."
    )


def main() -> int:
    args = parse_args()
    if is_disabled(os.environ.get("SD_AI_COMMAND_PACK_INSTALL_AUDIT")):
        print("warning: skipping install audit because SD_AI_COMMAND_PACK_INSTALL_AUDIT is disabled")
        return 0

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
    pack_manifest, manifest_failures = load_pack_manifest(root)
    failures.extend(manifest_failures)
    expected_count: int | None = None
    expected_platforms: set[str] = set()
    expected_warnings: list[str] = []
    if targets and not manifest_failures:
        (
            expected_failures,
            expected_warnings,
            expected_count,
            expected_platforms,
        ) = audit_expected_targets(
            root,
            targets,
            pack_manifest,
            explicit_platforms=args.expected_platform,
        )
        failures.extend(expected_failures)
    provenance_failures, provenance_version = audit_provenance(root)
    failures.extend(provenance_failures)
    warnings = [
        *structural_warnings,
        *expected_warnings,
        *audit_migration_advisories(root, targets),
    ]

    # Advisory warnings print even when the audit fails: the operator
    # debugging a failed audit is exactly who needs them.
    for warning in warnings:
        print(f"warning: {warning}")

    if args.upstream_manifest:
        installed_version = provenance_version
        if installed_version is None and isinstance(pack_manifest, dict):
            candidate = pack_manifest.get("version")
            if isinstance(candidate, str):
                installed_version = candidate
        print(version_update_advisory(installed_version, args.upstream_manifest))

    if failures:
        for failure in failures:
            print(f"error: {failure}")
        return 1

    print(f"SD AI command pack install audit passed: {len(targets)} targets checked.")
    if expected_count is not None:
        platforms = ", ".join(sorted(expected_platforms)) or "shared-only"
        print(
            "Expected target completeness: "
            f"{expected_count} manifest targets for {platforms}."
        )
    if provenance_version is not None:
        print(
            "Installed payload provenance: "
            f"version {provenance_version}; vouched file hashes match."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
