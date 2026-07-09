#!/usr/bin/env python3
"""Validate broad PR-body scope sections for SD AI command pack installs.

The checker is repo-safe by default: it reports detected categories, but only
fails when a PR body is supplied through ``--body-file``,
``SD_AI_COMMAND_PACK_PR_BODY_SCOPE_PR_BODY``, or
``SD_AI_COMMAND_PACK_SCOPE_PR_BODY``, or the deprecated compatibility fallback
``REVIEW_PREFLIGHT_PR_BODY``. Repos can add project-specific categories with a
JSON config file rather than editing this copied script.

Automated authors are exempt. When the PR author is passed via ``--actor`` or
``SD_AI_COMMAND_PACK_PR_BODY_SCOPE_ACTOR`` and the login is a bot (GitHub bot
logins carry the ``[bot]`` suffix — ``dependabot[bot]``,
``github-actions[bot]``, ``renovate[bot]``, …), the check reports the skip and
exits ``0`` even when a body is supplied. Without this, wiring the checker
into CI would fail every Dependabot/Renovate PR (their bodies never carry the
human scope headings) and block the auto-merge those bots rely on.

Config shape:

{
  "rules": [
    {
      "label": "Runtime/server scope",
      "headings": ["Runtime/server scope:", "Runtime scope:"],
      "patterns": ["src/**"]
    }
  ]
}

Exit codes:

* ``0`` - no issue found, no PR body was available to enforce, or the PR
  author is an exempt bot (``--actor`` / ``SD_AI_COMMAND_PACK_PR_BODY_SCOPE_ACTOR``).
* ``1`` - a supplied PR body is missing a required scope section.
* ``2`` - argument, git, config, or I/O error.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


BODY_ENV_VARS = (
    "SD_AI_COMMAND_PACK_PR_BODY_SCOPE_PR_BODY",
    "SD_AI_COMMAND_PACK_SCOPE_PR_BODY",
    "REVIEW_PREFLIGHT_PR_BODY",
)
CHANGED_FILES_ENV_VARS = (
    "SD_AI_COMMAND_PACK_PR_BODY_SCOPE_CHANGED_FILES",
    "SD_AI_COMMAND_PACK_CHANGED_FILES",
)
ACTOR_ENV_VARS = (
    "SD_AI_COMMAND_PACK_PR_BODY_SCOPE_ACTOR",
)
CONFIG_ENV_VAR = "SD_AI_COMMAND_PACK_PR_BODY_SCOPE_CONFIG"
DEFAULT_CONFIG_PATH = Path(".sd-ai-command-pack/pr-body-scope.json")
INSTALLED_TARGETS_FILE = Path(".sd-ai-command-pack/installed-targets.txt")


@dataclass(frozen=True)
class ScopeRule:
    label: str
    headings: tuple[str, ...]
    patterns: tuple[str, ...]
    include_installed_targets: bool = False


DEFAULT_RULES = (
    ScopeRule(
        label="Tooling/generated scope",
        headings=(
            "Tooling/generated scope:",
            "Generated/tooling scope:",
            "Copied/generated scope:",
        ),
        patterns=(
            ".sd-ai-command-pack/**",
            ".trellis/scripts/**",
            ".trellis/agents/**",
            ".github/agents/trellis-*.agent.md",
            ".github/copilot/hooks.json",
            ".github/copilot/hooks/**",
            ".github/hooks/trellis.json",
            ".github/prompts/**",
            ".github/skills/trellis-*/**",
            ".github/skills/sd-*/**",
            ".agent/skills/trellis-*/**",
            ".agent/skills/sd-*/**",
            ".agent/workflows/start.md",
            ".agent/workflows/continue.md",
            ".agent/workflows/finish-work.md",
            ".agent/workflows/sd-*.md",
            ".agents/skills/sd-*/**",
            ".agents/skills/trellis-*/**",
            ".claude/commands/sd/**",
            ".claude/commands/trellis/**",
            ".claude/skills/sd-*/**",
            ".claude/skills/trellis-*/**",
            ".codebuddy/commands/sd/**",
            ".codebuddy/commands/trellis/**",
            ".codebuddy/skills/sd-*/**",
            ".codebuddy/skills/trellis-*/**",
            ".cursor/commands/sd-*.md",
            ".cursor/commands/trellis-*.md",
            ".cursor/skills/sd-*/**",
            ".cursor/skills/trellis-*/**",
            ".devin/skills/sd-*/**",
            ".devin/skills/trellis-*/**",
            ".devin/workflows/sd-*.md",
            ".devin/workflows/trellis-*.md",
            ".factory/commands/sd/**",
            ".factory/commands/trellis/**",
            ".factory/skills/sd-*/**",
            ".factory/skills/trellis-*/**",
            ".gemini/agents/trellis-*.md",
            ".gemini/commands/sd/**",
            ".gemini/commands/trellis/**",
            ".gemini/skills/sd-*/**",
            ".gemini/skills/trellis-*/**",
            ".kiro/skills/sd-*/**",
            ".kiro/skills/trellis-*/**",
            ".kilocode/skills/sd-*/**",
            ".kilocode/skills/trellis-*/**",
            ".kilocode/workflows/start.md",
            ".kilocode/workflows/continue.md",
            ".kilocode/workflows/finish-work.md",
            ".kilocode/workflows/sd-*.md",
            ".opencode/commands/sd-*.md",
            ".opencode/commands/trellis/**",
            ".opencode/skills/sd-*/**",
            ".opencode/skills/trellis-*/**",
            ".pi/prompts/sd-*.md",
            ".pi/prompts/trellis-*.md",
            ".pi/skills/sd-*/**",
            ".pi/skills/trellis-*/**",
            ".qoder/commands/sd-*.md",
            ".qoder/commands/trellis-*.md",
            ".qoder/skills/sd-*/**",
            ".qoder/skills/trellis-*/**",
            ".reasonix/skills/sd-*/**",
            ".reasonix/skills/trellis-*/**",
            ".trae/commands/sd-*.md",
            ".trae/commands/trellis-*.md",
            ".trae/skills/sd-*/**",
            ".trae/skills/trellis-*/**",
            ".zcode/agents/trellis-*.md",
            ".zcode/cli/agents/trellis-*.md",
            ".zcode/commands/sd/**",
            ".zcode/commands/trellis/**",
            "docs/SD_AI_COMMAND_PACK.md",
            "docs/repomix-map.md",
            "scripts/sd-ai-command-pack-*.sh",
            "scripts/sd-ai-command-pack-*.py",
            "scripts/sd-ai-command-pack-full-check.sh",
            "scripts/sd-ai-command-pack-housekeeping.sh",
        ),
        include_installed_targets=True,
    ),
    ScopeRule(
        label="Automation scope",
        headings=("Automation scope:", "Housekeeping scope:"),
        patterns=(
            "scripts/sd-ai-command-pack-housekeeping.sh",
            ".agents/skills/sd-housekeeping/**",
            ".agent/skills/sd-housekeeping/**",
            ".agent/workflows/sd-housekeeping.md",
            ".codebuddy/commands/sd/housekeeping.md",
            ".codebuddy/skills/sd-housekeeping/**",
            ".github/prompts/sd-housekeeping.prompt.md",
            ".claude/commands/sd/housekeeping.md",
            ".cursor/commands/sd-housekeeping.md",
            ".devin/skills/sd-housekeeping/**",
            ".devin/workflows/sd-housekeeping.md",
            ".factory/commands/sd/housekeeping.md",
            ".factory/skills/sd-housekeeping/**",
            ".gemini/commands/sd/housekeeping.toml",
            ".kiro/skills/sd-housekeeping/**",
            ".kilocode/skills/sd-housekeeping/**",
            ".kilocode/workflows/sd-housekeeping.md",
            ".opencode/commands/sd-housekeeping.md",
            ".pi/prompts/sd-housekeeping.md",
            ".pi/skills/sd-housekeeping/**",
            ".qoder/commands/sd-housekeeping.md",
            ".qoder/skills/sd-housekeeping/**",
            ".reasonix/skills/sd-housekeeping/**",
            ".trae/commands/sd-housekeeping.md",
            ".trae/skills/sd-housekeeping/**",
            ".zcode/commands/sd/housekeeping.md",
        ),
    ),
    ScopeRule(
        label="CI/review scope",
        headings=("CI/review scope:", "CI scope:", "Workflow scope:"),
        patterns=(
            ".github/workflows/**",
            ".pre-commit-config.yaml",
            "scripts/classify-ci-changes.sh",
            "scripts/classify_ci_changes.sh",
            "scripts/check-review-preflight.mjs",
            "scripts/sd-ai-command-pack-review-scope.sh",
            "scripts/sd-ai-command-pack-review-local.sh",
            "scripts/sd-ai-command-pack-review-learnings.py",
            "scripts/sd-ai-command-pack-install-audit.py",
            "scripts/sd-ai-command-pack-pr-body-scope.py",
            "scripts/sd-ai-command-pack-full-check.sh",
        ),
    ),
)


def _normalize_path(path: str) -> str:
    return _remove_dot_slash(path.replace("\\", "/"))


def _remove_dot_slash(path: str) -> str:
    return path[2:] if path.startswith("./") else path


def _split_changed_files(text: str) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for raw_path in text.replace("\0", "\n").splitlines():
        stripped_path = raw_path.strip()
        if not stripped_path:
            continue
        path = _normalize_path(stripped_path)
        if path and path not in seen:
            paths.append(path)
            seen.add(path)
    return paths


def _run_git(root: Path, *args: str) -> tuple[int, str, str]:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return result.returncode, result.stdout, result.stderr


def _collect_git_changed_files(root: Path) -> tuple[list[str], str | None]:
    code, _stdout, stderr = _run_git(root, "rev-parse", "--is-inside-work-tree")
    if code != 0:
        return [], f"{root} is not a git worktree: {stderr.strip()}"

    outputs: list[str] = []
    for args in [
        ("diff", "--name-only"),
        ("diff", "--cached", "--name-only"),
        ("ls-files", "--others", "--exclude-standard"),
    ]:
        code, stdout, stderr = _run_git(root, *args)
        if code != 0:
            return [], f"git {' '.join(args)} failed: {stderr.strip()}"
        outputs.append(stdout)
    return _split_changed_files("\n".join(outputs)), None


def _load_changed_files(
    root: Path,
    changed_files_path: Path | None,
) -> tuple[list[str], str | None]:
    if changed_files_path is not None:
        try:
            return _split_changed_files(changed_files_path.read_text(encoding="utf-8", errors="replace")), None
        except OSError as exc:
            return [], f"cannot read changed files list {changed_files_path}: {exc}"
        except UnicodeError as exc:
            return [], f"cannot decode changed files list {changed_files_path}: {exc}"

    for env_var in CHANGED_FILES_ENV_VARS:
        if env_var in os.environ:
            return _split_changed_files(os.environ[env_var]), None

    return _collect_git_changed_files(root)


def _load_body(body_file: Path | None) -> tuple[str | None, str | None]:
    if body_file is not None:
        try:
            return body_file.read_text(encoding="utf-8", errors="replace"), None
        except OSError as exc:
            return None, f"cannot read PR body file {body_file}: {exc}"
        except UnicodeError as exc:
            return None, f"cannot decode PR body file {body_file}: {exc}"

    for env_var in BODY_ENV_VARS:
        if env_var in os.environ:
            return os.environ[env_var], None

    return None, None


def _matches_pattern(normalized_path: str, pattern: str) -> bool:
    """Match a normalized repository path against a PR-body scope glob."""
    normalized_pattern = _remove_dot_slash(pattern.replace("\\", "/"))
    if normalized_pattern.endswith("/**"):
        # fnmatch expands glob characters in the base (e.g. sd-*), and its
        # "*" crosses "/" so f"{base}/*" covers arbitrary depth under it.
        base_pattern = normalized_pattern[:-3].rstrip("/")
        return fnmatch.fnmatchcase(normalized_path, base_pattern) or fnmatch.fnmatchcase(
            normalized_path, f"{base_pattern}/*"
        )
    return fnmatch.fnmatchcase(normalized_path, normalized_pattern)


def _load_installed_target_patterns(root: Path) -> tuple[tuple[str, ...], str | None]:
    targets_path = root / INSTALLED_TARGETS_FILE
    if not targets_path.is_file():
        return (), None
    try:
        targets = _split_changed_files(targets_path.read_text(encoding="utf-8", errors="replace"))
    except OSError as exc:
        return (), f"cannot read installed targets file {targets_path}: {exc}"
    except UnicodeError as exc:
        return (), f"cannot decode installed targets file {targets_path}: {exc}"
    return tuple(targets), None


def _load_config_rules(config_path: Path | None) -> tuple[tuple[ScopeRule, ...], str | None]:
    if config_path is None:
        return (), None
    if not config_path.is_file():
        return (), None
    try:
        raw: Any = json.loads(config_path.read_text(encoding="utf-8", errors="replace"))
    except OSError as exc:
        return (), f"cannot read PR body scope config {config_path}: {exc}"
    except UnicodeError as exc:
        return (), f"cannot decode PR body scope config {config_path}: {exc}"
    except json.JSONDecodeError as exc:
        return (), f"cannot parse PR body scope config {config_path}: {exc}"

    raw_rules = raw.get("rules") if isinstance(raw, dict) else None
    if not isinstance(raw_rules, list):
        return (), f"{config_path}: expected object with a rules list"

    rules: list[ScopeRule] = []
    for index, item in enumerate(raw_rules, 1):
        if not isinstance(item, dict):
            return (), f"{config_path}: rule {index} must be an object"
        label = item.get("label")
        headings = item.get("headings")
        patterns = item.get("patterns")
        include_installed_targets = item.get("include_installed_targets", False)
        if not isinstance(label, str) or not label.strip():
            return (), f"{config_path}: rule {index} needs a non-empty label"
        if (
            not isinstance(headings, list)
            or not headings
            or not all(isinstance(value, str) and value.strip() for value in headings)
        ):
            return (
                (),
                f"{config_path}: rule {index} needs a non-empty list of "
                "non-empty string headings",
            )
        if not isinstance(patterns, list) or not all(isinstance(value, str) and value.strip() for value in patterns):
            return (), f"{config_path}: rule {index} needs non-empty string patterns"
        if not isinstance(include_installed_targets, bool):
            return (), f"{config_path}: rule {index} include_installed_targets must be boolean"
        rules.append(
            ScopeRule(
                label=label.strip(),
                headings=tuple(value.strip() for value in headings),
                patterns=tuple(value.strip() for value in patterns),
                include_installed_targets=include_installed_targets,
            )
        )
    return tuple(rules), None


def _resolve_config_path(root: Path, explicit_config: Path | None) -> tuple[Path, bool]:
    if explicit_config is not None:
        path = explicit_config if explicit_config.is_absolute() else root / explicit_config
        return path, True
    if CONFIG_ENV_VAR in os.environ:
        env_path = Path(os.environ[CONFIG_ENV_VAR])
        return (env_path if env_path.is_absolute() else root / env_path), True
    return root / DEFAULT_CONFIG_PATH, False


def _merge_rules(rules: tuple[ScopeRule, ...]) -> tuple[ScopeRule, ...]:
    """Merge rules with identical labels/headings while preserving first order."""
    merged: dict[tuple[str, tuple[str, ...]], list[str]] = {}
    seen_patterns: dict[tuple[str, tuple[str, ...]], set[str]] = {}
    include_installed_targets: dict[tuple[str, tuple[str, ...]], bool] = {}
    order: list[tuple[str, tuple[str, ...]]] = []
    for rule in rules:
        key = (rule.label, rule.headings)
        if key not in merged:
            merged[key] = []
            seen_patterns[key] = set()
            include_installed_targets[key] = False
            order.append(key)
        include_installed_targets[key] = (
            include_installed_targets[key] or rule.include_installed_targets
        )
        for pattern in rule.patterns:
            if pattern not in seen_patterns[key]:
                seen_patterns[key].add(pattern)
                merged[key].append(pattern)

    return tuple(
        ScopeRule(
            label=label,
            headings=headings,
            patterns=tuple(merged[(label, headings)]),
            include_installed_targets=include_installed_targets[(label, headings)],
        )
        for label, headings in order
    )


def _include_installed_targets(
    rules: tuple[ScopeRule, ...],
    installed_targets: tuple[str, ...],
) -> tuple[ScopeRule, ...]:
    if not installed_targets:
        return rules
    return tuple(
        ScopeRule(
            label=rule.label,
            headings=rule.headings,
            patterns=rule.patterns + installed_targets,
            include_installed_targets=rule.include_installed_targets,
        )
        if rule.include_installed_targets
        else rule
        for rule in rules
    )


def _rules_for_repo(root: Path, explicit_config: Path | None) -> tuple[tuple[ScopeRule, ...], str | None]:
    """Load default and optional repo-specific rules for the target repository."""
    installed_targets, target_error = _load_installed_target_patterns(root)
    if target_error is not None:
        return (), target_error

    default_rules = _include_installed_targets(DEFAULT_RULES, installed_targets)

    config_path, config_explicit = _resolve_config_path(root, explicit_config)
    if config_explicit and not config_path.is_file():
        return (), f"PR body scope config not found: {config_path}"
    config_rules, config_error = _load_config_rules(config_path)
    if config_error is not None:
        return (), config_error

    config_rules = _include_installed_targets(config_rules, installed_targets)

    return _merge_rules(default_rules + config_rules), None


def _classify(paths: list[str], rules: tuple[ScopeRule, ...]) -> dict[ScopeRule, list[str]]:
    """Group changed normalized paths by every scope rule they match."""
    matches: dict[ScopeRule, list[str]] = {}
    for path in paths:
        for rule in rules:
            if any(_matches_pattern(path, pattern) for pattern in rule.patterns):
                matches.setdefault(rule, []).append(path)
    return matches


def _body_has_heading(body: str, headings: tuple[str, ...]) -> bool:
    # Require the heading to start a line (optionally behind Markdown heading,
    # blockquote, or list markers) so a passing mention in prose, a URL, or a
    # code span does not satisfy the section requirement.
    for heading in headings:
        normalized = heading.strip()
        if normalized.endswith(":"):
            normalized = normalized[:-1].rstrip()
        pattern = re.compile(
            r"^[ \t]*[>#*\-]*[ \t]*"
            + re.escape(normalized)
            + r"(?::.*|[ \t]*)$",
            re.IGNORECASE | re.MULTILINE,
        )
        if pattern.search(body):
            return True
    return False


def _resolve_actor(explicit: str | None) -> str:
    """Resolve the PR author login from the flag, then the env fallback."""
    if explicit and explicit.strip():
        return explicit.strip()
    for var in ACTOR_ENV_VARS:
        value = os.environ.get(var)
        if value and value.strip():
            return value.strip()
    return ""


def _actor_is_exempt(actor: str) -> bool:
    """True when the PR author is automation and must not be held to the
    PR-body scope contract.

    GitHub bot logins carry the ``[bot]`` suffix (``dependabot[bot]``,
    ``github-actions[bot]``, ``renovate[bot]``, …), so a single suffix check
    covers every bot that opens PRs. A non-``[bot]`` service account that
    needs the same exemption should be handled by the caller (skip the step
    for that actor); keeping this predicate to the universal ``[bot]``
    convention avoids a per-repo actor allowlist the pack would have to
    maintain.
    """
    return bool(actor) and actor.endswith("[bot]")


def check(
    root: Path,
    *,
    body_file: Path | None = None,
    changed_files_path: Path | None = None,
    config_path: Path | None = None,
    actor: str | None = None,
) -> tuple[int, list[str]]:
    """Run the PR body scope check and return an exit code plus messages."""
    changed_files, changed_error = _load_changed_files(root, changed_files_path)
    if changed_error is not None:
        return 2, [changed_error]

    rules, rules_error = _rules_for_repo(root, config_path)
    if rules_error is not None:
        return 2, [rules_error]

    scoped_changes = _classify(changed_files, rules)
    if not scoped_changes:
        return 0, ["info: no changed files matched PR-body scope categories."]

    body, body_error = _load_body(body_file)
    if body_error is not None:
        return 2, [body_error]

    detected = [
        f"info: detected {rule.label} paths: {', '.join(paths[:5])}"
        for rule, paths in scoped_changes.items()
    ]

    resolved_actor = _resolve_actor(actor)
    if _actor_is_exempt(resolved_actor):
        return 0, [
            *detected,
            f"info: PR-body scope check skipped for automated actor "
            f"'{resolved_actor}'.",
        ]

    if body is None:
        return 0, [
            *detected,
            "warning: PR body not provided; skipping strict PR-body scope validation. "
            "Set SD_AI_COMMAND_PACK_PR_BODY_SCOPE_PR_BODY, "
            "SD_AI_COMMAND_PACK_SCOPE_PR_BODY, REVIEW_PREFLIGHT_PR_BODY, or "
            "--body-file.",
        ]

    # Any-of coverage: a changed path is covered when the body documents at
    # least one of the scope categories it falls under, so a single file that
    # matches several rules never has to carry every section heading at once.
    present_rules = {
        rule for rule in scoped_changes if _body_has_heading(body, rule.headings)
    }
    covered_paths: set[str] = set()
    for rule in present_rules:
        covered_paths.update(scoped_changes[rule])

    violations: list[str] = []
    for rule, paths in scoped_changes.items():
        if rule in present_rules:
            continue
        uncovered = [path for path in paths if path not in covered_paths]
        if not uncovered:
            continue
        headings = " or ".join(rule.headings)
        changed = ", ".join(uncovered[:5])
        violations.append(
            f"missing {rule.label} section ({headings}) for changed paths: {changed}"
        )

    if violations:
        return 1, [*detected, *violations]

    return 0, [*detected, "info: PR body scope sections cover detected change categories."]


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo_root", nargs="?", default=".", help="repository root")
    parser.add_argument("--body-file", type=Path, help="file containing the PR body")
    parser.add_argument(
        "--changed-files",
        type=Path,
        help="newline- or NUL-delimited changed path list",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help=(
            "JSON config with additional rules. Defaults to "
            ".sd-ai-command-pack/pr-body-scope.json when present."
        ),
    )
    parser.add_argument(
        "--actor",
        help=(
            "PR author login. A bot login (ending in '[bot]', e.g. "
            "dependabot[bot]) is exempt from strict scope validation. "
            "Falls back to SD_AI_COMMAND_PACK_PR_BODY_SCOPE_ACTOR."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv[1:])
    root = Path(args.repo_root).resolve()
    status, messages = check(
        root,
        body_file=args.body_file,
        changed_files_path=args.changed_files,
        config_path=args.config,
        actor=args.actor,
    )
    if messages:
        print("\n".join(messages), file=sys.stderr if status else sys.stdout)
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv))
