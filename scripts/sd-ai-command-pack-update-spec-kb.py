#!/usr/bin/env python3
"""Build a repo-local Obsidian knowledge-base copy folder.

The update-spec workflow uses this helper to expose portable repository
knowledge files to Obsidian. It maintains a generated `.obsidian-kb/` folder
with copies of the source documents, a dashboard of the current copies, a
self-contained LLM overview, and the matching ignore entry in either
`.gitignore` or `.git/info/exclude` for local-only installs. Generated copies
are intentionally limited to
documentation, repository maps, Trellis spec/context files, and project
manifests; caches, secrets, source trees, build output, and Trellis workspace
state stay out.
"""

from __future__ import annotations

import argparse
import filecmp
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote, urlparse


KB_DIR = Path(".obsidian-kb")
LEGACY_KB_DASHBOARD = Path("Dashboard.md")
LEGACY_KB_OVERVIEW = Path("LLM-KB.md")
GITIGNORE_ENTRY = ".obsidian-kb/"
LOCAL_ONLY_MARKER_FILE = Path(".sd-ai-command-pack/local-only.txt")
KB_IGNORE_BLOCK_START = "# sd-ai-command-pack obsidian-kb start"
KB_IGNORE_BLOCK_END = "# sd-ai-command-pack obsidian-kb end"
DASHBOARD_MARKER = "<!-- SD-AI-COMMAND-PACK:OBSIDIAN-KB-DASHBOARD -->"
OVERVIEW_MARKER = "<!-- SD-AI-COMMAND-PACK:LLM-KB-OVERVIEW -->"
DOC_SUFFIXES = {".adoc", ".md", ".mdx", ".rst", ".txt"}
MAP_SUFFIXES = DOC_SUFFIXES | {".json", ".xml", ".yaml", ".yml"}
ROOT_DOC_PREFIXES = (
    "AGENTS",
    "ARCHITECTURE",
    "CHANGELOG",
    "CONTRIBUTING",
    "DECISION",
    "DECISIONS",
    "DEVELOPMENT",
    "HANDOFF",
    "README",
    "ROADMAP",
)
PROJECT_MANIFESTS = {
    "Cargo.toml",
    "Gemfile",
    "Justfile",
    "Makefile",
    "Package.swift",
    "build.gradle",
    "composer.json",
    "deno.json",
    "deno.jsonc",
    "go.mod",
    "go.work",
    "lerna.json",
    "mix.exs",
    "nx.json",
    "package.json",
    "pnpm-workspace.yaml",
    "pom.xml",
    "pyproject.toml",
    "requirements.txt",
    "rush.json",
    "settings.gradle",
    "turbo.json",
    "workspace.json",
}
PLATFORM_GUIDANCE_ROOTS = {
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
    ".kiro",
    ".kilocode",
    ".opencode",
    ".pi",
    ".qoder",
    ".reasonix",
    ".trae",
    ".zcode",
}
PLATFORM_GUIDANCE_FILENAMES = {
    "AGENTS.md",
    "CLAUDE.md",
    "GEMINI.md",
    "README.md",
    "SKILL.md",
    "config.toml",
    "copilot-instructions.md",
    "hooks.json",
    "package.json",
    "settings.json",
}
PLATFORM_GUIDANCE_SUFFIXES = DOC_SUFFIXES | {".json", ".toml", ".yaml", ".yml"}
EXCLUDED_PARTS = {
    ".cache",
    ".git",
    ".hg",
    ".mypy_cache",
    ".next",
    ".obsidian",
    ".obsidian-kb",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "htmlcov",
    "node_modules",
    "target",
    "vendor",
    "venv",
}


def repo_root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        text=True,
        encoding="utf-8",
        errors="surrogateescape",
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode == 0 and result.stdout.strip():
        return Path(result.stdout.strip()).resolve()
    print(
        "warning: not inside a git repository; using the current directory as "
        "the repo root.",
        file=sys.stderr,
    )
    return Path.cwd().resolve()


def git_remote_url(root: Path) -> str | None:
    result = subprocess.run(
        ["git", "config", "--get", "remote.origin.url"],
        cwd=root,
        text=True,
        encoding="utf-8",
        errors="surrogateescape",
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        return None
    remote = result.stdout.strip()
    return remote or None


def github_repository_url_from_remote(remote_url: str | None) -> str | None:
    if remote_url is None:
        return None
    remote = remote_url.strip()
    if not remote:
        return None

    slug: str | None = None
    if remote.startswith("git@github.com:"):
        slug = remote.removeprefix("git@github.com:")
    else:
        parsed = urlparse(remote)
        if parsed.hostname == "github.com":
            slug = parsed.path.lstrip("/")

    if slug is None:
        return None
    slug = slug.strip("/")
    if slug.endswith(".git"):
        slug = slug[:-4]
    parts = [part for part in slug.split("/") if part]
    if len(parts) < 2:
        return None
    owner, repo = parts[:2]
    return f"https://github.com/{owner}/{repo}"


def github_repository_url(root: Path) -> str | None:
    return github_repository_url_from_remote(git_remote_url(root))


def github_link_text(github_url: str) -> str:
    return github_url.removeprefix("https://github.com/")


def dashboard_path(root: Path) -> Path:
    return Path(f"Dashboard - {root.name}.md")


def overview_path(root: Path) -> Path:
    return Path(f"LLM-KB - {root.name}.md")


def generated_kb_files(root: Path) -> set[Path]:
    return {dashboard_path(root), overview_path(root)}


def legacy_generated_marker(path: Path) -> str | None:
    if path == LEGACY_KB_DASHBOARD:
        return DASHBOARD_MARKER
    if path == LEGACY_KB_OVERVIEW:
        return OVERVIEW_MARKER
    return None


def is_managed_kb_category_path(path: Path) -> bool:
    return bool(path.parts) and path.parts[0] in {
        title for _, title, _ in KB_CATEGORIES
    }


def is_legacy_source_parent(path: Path, legacy_sources: set[Path]) -> bool:
    if not path.parts:
        return False
    return any(
        len(source.parts) > len(path.parts)
        and source.parts[: len(path.parts)] == path.parts
        for source in legacy_sources
    )


def is_stale_generated_kb_entry(
    candidate: Path,
    relative_candidate: Path,
    root: Path,
    legacy_sources: set[Path],
) -> bool:
    legacy_marker = legacy_generated_marker(relative_candidate)
    if legacy_marker is not None:
        return file_contains_marker(candidate, legacy_marker)
    if candidate.is_symlink():
        return (
            is_tool_owned_symlink(candidate, root)
            and (
                is_managed_kb_category_path(relative_candidate)
                or relative_candidate in legacy_sources
            )
        )
    return candidate.is_file() and is_managed_kb_category_path(relative_candidate)


def is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def is_excluded(path: Path) -> bool:
    parts = path.parts
    if any(part in EXCLUDED_PARTS for part in parts):
        return True
    # Trellis runtime and backup artifacts must never feed KB source
    # discovery; durable .trellis knowledge (spec/tasks/workflow) stays
    # eligible through the path-aware checks below.
    if any(part.startswith(".backup-") or part == ".runtime" for part in parts):
        return True
    if parts[:1] == (".trellis",) and "worktrees" in parts[1:]:
        return True
    return len(parts) >= 2 and parts[0] == ".trellis" and parts[1] == "workspace"


def is_named_doc(path: Path) -> bool:
    upper_name = path.name.upper()
    if path.suffix and path.suffix.lower() not in DOC_SUFFIXES:
        return False
    return (
        any(
            upper_name == prefix
            or upper_name.startswith(f"{prefix}.")
            or upper_name.startswith(f"{prefix}-")
            or upper_name.startswith(f"{prefix}_")
            for prefix in ROOT_DOC_PREFIXES
        )
    )


def is_docs_file(path: Path) -> bool:
    return path.parts[:1] == ("docs",) and path.suffix.lower() in DOC_SUFFIXES


def is_trellis_knowledge(path: Path) -> bool:
    return path in {
        Path(".trellis/config.yaml"),
        Path(".trellis/config.yml"),
        Path(".trellis/workflow.md"),
    } or (
        len(path.parts) == 3
        and path.parts[0] == ".trellis"
        and path.parts[1] == "agents"
        and path.suffix.lower() in DOC_SUFFIXES
    ) or (
        len(path.parts) >= 3
        and path.parts[0] == ".trellis"
        and path.parts[1] == "tasks"
        and path.suffix.lower() in DOC_SUFFIXES
    ) or (
        len(path.parts) >= 3
        and path.parts[0] == ".trellis"
        and path.parts[1] == "spec"
        and path.suffix.lower() in DOC_SUFFIXES
    )


def is_platform_guidance(path: Path) -> bool:
    parts = path.parts
    if not parts or parts[0] not in PLATFORM_GUIDANCE_ROOTS:
        return False
    if path.suffix.lower() not in PLATFORM_GUIDANCE_SUFFIXES:
        return False
    if path.name in PLATFORM_GUIDANCE_FILENAMES:
        return True
    if "instructions" in parts and path.suffix.lower() in DOC_SUFFIXES:
        return True
    if "prompts" in parts and path.suffix.lower() in DOC_SUFFIXES:
        return True
    if "commands" in parts and path.suffix.lower() in {".md", ".toml"}:
        return (
            path.stem.startswith(("sd-", "trellis-"))
            or "sd" in parts
            or "trellis" in parts
        )
    if "workflows" in parts and path.suffix.lower() in DOC_SUFFIXES:
        return path.stem.startswith(("sd-", "trellis-")) or path.stem in {
            "start",
            "continue",
            "finish-work",
        }
    if path.name == "SKILL.md" and len(parts) >= 3:
        return any(part.startswith(("sd-", "trellis-")) for part in parts)
    return False


def is_repo_map(path: Path) -> bool:
    lower_name = path.name.lower()
    return (
        ("repomix" in lower_name or "repospec" in lower_name or "repo-map" in lower_name)
        and path.suffix.lower() in MAP_SUFFIXES
    )


def is_project_manifest(path: Path) -> bool:
    return path.name in PROJECT_MANIFESTS


def is_relevant(path: Path) -> bool:
    return (
        is_named_doc(path)
        or is_docs_file(path)
        or is_trellis_knowledge(path)
        or is_platform_guidance(path)
        or is_repo_map(path)
        or is_project_manifest(path)
    )


def discover_sources(root: Path) -> list[Path]:
    sources: list[Path] = []
    for current_dir, dirnames, filenames in os.walk(root):
        current = Path(current_dir)
        relative_dir = current.relative_to(root)
        dirnames[:] = [
            dirname
            for dirname in dirnames
            if not is_excluded(relative_dir / dirname)
        ]
        for filename in filenames:
            candidate = current / filename
            relative = candidate.relative_to(root)
            if is_excluded(relative):
                continue
            if candidate.is_symlink() or not candidate.is_file():
                continue
            if is_relevant(relative):
                sources.append(relative)
    return sorted(set(sources), key=lambda path: path.as_posix())


def _entry_ignores_kb(entry: str) -> bool:
    stripped = entry.strip()
    if not stripped or stripped.startswith("#"):
        return False
    normalized = stripped.lstrip("/").rstrip("/")
    if normalized.endswith("/*"):
        normalized = normalized[:-2]
    return normalized == KB_DIR.as_posix()


def kb_ignore_block() -> str:
    return "\n".join(
        (
            KB_IGNORE_BLOCK_START,
            "# Generated by scripts/sd-ai-command-pack-update-spec-kb.py. DO NOT EDIT MANUALLY.",
            "# Generated Obsidian KB copy folder; source docs remain in normal repo paths.",
            GITIGNORE_ENTRY,
            KB_IGNORE_BLOCK_END,
        )
    ) + "\n"


def _remove_unmanaged_kb_entries(current: str) -> tuple[str, bool]:
    lines = current.splitlines(keepends=True)
    kept: list[str] = []
    removed = False
    for line in lines:
        if _entry_ignores_kb(line):
            removed = True
            continue
        kept.append(line)
    return "".join(kept), removed


def merge_kb_ignore_block(current: str) -> tuple[str, bool]:
    block = kb_ignore_block()
    start_index = current.find(KB_IGNORE_BLOCK_START)
    end_index = current.find(KB_IGNORE_BLOCK_END)
    has_start = start_index != -1
    has_end = end_index != -1
    if has_start != has_end or (has_start and end_index < start_index):
        raise SystemExit(
            "error: ignore file has incomplete sd-ai-command-pack "
            "obsidian-kb markers"
        )
    if has_start:
        replace_end = end_index + len(KB_IGNORE_BLOCK_END)
        if replace_end < len(current) and current[replace_end] == "\n":
            replace_end += 1
        return current[:start_index] + block + current[replace_end:], True

    prefix, had_unmanaged_entry = _remove_unmanaged_kb_entries(current)
    if not prefix:
        return block, had_unmanaged_entry
    if not prefix.endswith("\n"):
        prefix += "\n"
    if not prefix.endswith("\n\n"):
        prefix += "\n"
    return prefix + block, had_unmanaged_entry


def read_text_if_present(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="surrogateescape")
    except FileNotFoundError:
        return None


def file_contains_marker(path: Path, marker: str) -> bool:
    if not path.is_file() or path.is_symlink():
        return False
    text = read_text_if_present(path)
    return text is not None and marker in text


def ignore_present_state(*, local: bool) -> str:
    return "local-exclude present" if local else "present"


def ignore_change_state(*, local: bool, had_existing_entry: bool) -> str:
    status = "updated" if had_existing_entry else "added"
    return f"local-exclude {status}" if local else status


def ensure_ignore_file(path: Path, *, local: bool) -> str:
    original = read_text_if_present(path) or ""
    merged, had_existing_entry = merge_kb_ignore_block(original)
    if merged == original:
        return ignore_present_state(local=local)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(merged, encoding="utf-8", errors="surrogateescape")
    return ignore_change_state(local=local, had_existing_entry=had_existing_entry)


def git_info_exclude_path(root: Path) -> Path | None:
    result = subprocess.run(
        ["git", "rev-parse", "--git-path", "info/exclude"],
        cwd=root,
        text=True,
        encoding="utf-8",
        errors="surrogateescape",
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    path = Path(result.stdout.strip())
    if not path.is_absolute():
        path = root / path
    return path


def ensure_local_exclude(root: Path) -> str:
    exclude = git_info_exclude_path(root)
    if exclude is None:
        return ensure_repo_gitignore(root)
    return ensure_ignore_file(exclude, local=True)


def ensure_repo_gitignore(root: Path) -> str:
    return ensure_ignore_file(root / ".gitignore", local=False)


def ensure_gitignore(root: Path) -> str:
    if (root / LOCAL_ONLY_MARKER_FILE).is_file():
        return ensure_local_exclude(root)
    return ensure_repo_gitignore(root)


def planned_ignore_file_state(path: Path, *, local: bool) -> str:
    original = read_text_if_present(path) or ""
    merged, had_existing_entry = merge_kb_ignore_block(original)
    if merged == original:
        return ignore_present_state(local=local)
    return ignore_change_state(local=local, had_existing_entry=had_existing_entry)


def planned_gitignore_state(root: Path) -> str:
    if (root / LOCAL_ONLY_MARKER_FILE).is_file():
        exclude = git_info_exclude_path(root)
        if exclude is None:
            return planned_ignore_file_state(root / ".gitignore", local=False)
        return planned_ignore_file_state(exclude, local=True)
    return planned_ignore_file_state(root / ".gitignore", local=False)


def expected_link_target(root: Path, relative_source: Path, relative_destination: Path) -> str:
    source = root / relative_source
    link = root / KB_DIR / relative_destination
    return os.path.relpath(source, link.parent)


def is_tool_owned_symlink(path: Path, root: Path) -> bool:
    current = os.readlink(path)
    return not os.path.isabs(current) and is_within(path.parent / current, root)


def legacy_generated_symlink_count(root: Path) -> int:
    kb_root = root / KB_DIR
    if not kb_root.exists():
        return 0

    generated = generated_kb_files(root)
    count = 0
    for candidate in kb_root.rglob("*"):
        relative_candidate = candidate.relative_to(kb_root)
        if relative_candidate in generated:
            continue
        if candidate.is_symlink() and is_tool_owned_symlink(candidate, root):
            count += 1
    return count


def collect_copy_state(root: Path, sources: list[Path]) -> tuple[int, int, list[str]]:
    kb_root = root / KB_DIR
    entries = source_destination_entries(sources)
    wanted = {destination for _, destination in entries}
    legacy_sources = {source for source, _ in entries}
    generated = generated_kb_files(root)
    present = 0
    stale = 0
    issues: list[str] = []

    if kb_root.exists():
        for candidate in sorted(kb_root.rglob("*"), reverse=True):
            relative_candidate = candidate.relative_to(kb_root)
            if relative_candidate in wanted or relative_candidate in generated:
                continue
            if is_stale_generated_kb_entry(
                candidate,
                relative_candidate,
                root,
                legacy_sources,
            ):
                stale += 1

    for relative_source, relative_destination in entries:
        copy = kb_root / relative_destination
        source = root / relative_source
        if copy.is_symlink():
            current = os.readlink(copy)
            if os.path.isabs(current) or not is_within(copy.parent / current, root):
                issues.append(f"{relative_destination.as_posix()} has a user-owned symlink")
                continue
            target = expected_link_target(root, relative_source, relative_destination)
            if current == target:
                issues.append(
                    f"{relative_destination.as_posix()} is a legacy generated symlink"
                )
            else:
                issues.append(f"{relative_destination.as_posix()} points at {current!r}")
        elif copy.exists():
            if not copy.is_file():
                issues.append(f"{relative_destination.as_posix()} is occupied by a non-file")
            elif filecmp.cmp(source, copy, shallow=False):
                present += 1
            else:
                issues.append(f"{relative_destination.as_posix()} is not current")
        else:
            issues.append(f"{relative_destination.as_posix()} is missing")

    return present, stale, issues


def prune_stale_kb_entries(
    kb_root: Path,
    wanted: set[Path],
    root: Path,
    legacy_sources: set[Path],
) -> int:
    if not kb_root.exists():
        return 0
    generated = generated_kb_files(root)
    removed = 0
    for candidate in sorted(kb_root.rglob("*"), reverse=True):
        relative_candidate = candidate.relative_to(kb_root)
        if relative_candidate in wanted or relative_candidate in generated:
            continue
        if is_stale_generated_kb_entry(
            candidate,
            relative_candidate,
            root,
            legacy_sources,
        ):
            candidate.unlink()
            removed += 1
        elif candidate.is_dir():
            if not (
                is_managed_kb_category_path(relative_candidate)
                or is_legacy_source_parent(relative_candidate, legacy_sources)
            ):
                continue
            try:
                candidate.rmdir()
            except OSError:
                pass
    return removed


def markdown_link_text(path: Path) -> str:
    return path.name.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def markdown_path_link_text(path: Path) -> str:
    label = path.name
    return label.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def markdown_link_target(path: Path) -> str:
    return quote(path.as_posix(), safe="/._-~")


def source_description(path: Path) -> str:
    parts = path.parts
    lower_name = path.name.lower()
    upper_name = path.name.upper()

    if path == Path("README.md"):
        return "Repository overview and primary entrypoint."
    if path == Path("AGENTS.md"):
        return "Project instructions for AI coding agents."
    if path == Path("docs/SD_AI_COMMAND_PACK.md"):
        return "Detailed command-pack reference and usage guide."
    if is_repo_map(path):
        return "Repository map or repospec artifact for broad codebase context."
    if upper_name.startswith("ARCHITECTURE"):
        return "Architecture context and system-shape notes."
    if upper_name.startswith(("DECISION", "DECISIONS")):
        return "Decision record for important project tradeoffs."
    if upper_name.startswith("ROADMAP"):
        return "Roadmap context for planned or staged work."
    if upper_name.startswith("HANDOFF"):
        return "Handoff notes for continuing work safely."
    if path == Path(".trellis/workflow.md"):
        return "Workflow phases, task rules, and routing guidance."
    if path in {Path(".trellis/config.yaml"), Path(".trellis/config.yml")}:
        return "Project configuration and spec routing."
    if len(parts) >= 3 and parts[0] == ".trellis" and parts[1] == "tasks":
        if lower_name == "prd.md":
            return "Task requirements, scope, and acceptance criteria."
        if lower_name == "design.md":
            return "Task design notes and implementation approach."
        if lower_name == "implement.md":
            return "Task implementation notes and execution context."
        if lower_name == "check.md":
            return "Task verification notes and quality-gate context."
        return "Task documentation and delivery context."
    if len(parts) >= 3 and parts[0] == ".trellis" and parts[1] == "spec":
        layer = parts[2]
        if layer == "guides":
            if lower_name == "index.md":
                return "Index for thinking guides."
            return "Thinking checklist for future implementation work."
        if lower_name == "index.md":
            return f"Index for the {layer} spec layer."
        return f"{layer.title()} code-spec guidance for future implementation."
    if parts and parts[0] in PLATFORM_GUIDANCE_ROOTS:
        return "AI-platform guidance or adapter metadata."
    if is_project_manifest(path):
        return "Project manifest describing package, build, or workspace shape."
    if "README" in upper_name and path.parent != Path("."):
        return "Package-level documentation entrypoint."
    return "Curated repository knowledge document."


KB_CATEGORIES: tuple[tuple[str, str, str], ...] = (
    (
        "repository-overview",
        "Repository Overview",
        "Top-level documents that orient a reader to the repository.",
    ),
    (
        "agent-platform-guidance",
        "Agent and Platform Guidance",
        "Instructions and platform files that shape AI-agent behavior.",
    ),
    (
        "pack-documentation",
        "Pack Documentation",
        "Detailed documentation for this command pack and its workflows.",
    ),
    (
        "architecture-decisions",
        "Architecture and Decisions",
        "Architecture, roadmap, handoff, and decision-oriented documents.",
    ),
    (
        "trellis-workflow-config",
        "Workflow and Configuration",
        "Workflow and repository configuration context.",
    ),
    (
        "task-documentation",
        "Task Documentation",
        "Task PRDs, designs, implementation notes, and verification context.",
    ),
    (
        "trellis-backend-specs",
        "Backend Specs",
        "Executable contracts for installer, filesystem, and local tooling behavior.",
    ),
    (
        "trellis-frontend-specs",
        "Frontend Specs",
        "Executable contracts for prompt, adapter, and user-facing workflow text.",
    ),
    (
        "trellis-thinking-guides",
        "Thinking Guides",
        "Reusable thinking checklists that point future work toward the specs.",
    ),
    (
        "repository-maps",
        "Repository Maps",
        "Generated or maintained repository-map artifacts.",
    ),
    (
        "project-manifests",
        "Project Manifests",
        "Build, package, and workspace manifests that explain project shape.",
    ),
    (
        "package-documentation",
        "Package Documentation",
        "Package-level README and local documentation entrypoints.",
    ),
    (
        "other-documentation",
        "Other Documentation",
        "Additional curated repository knowledge files.",
    ),
)
KB_CATEGORY_BY_KEY = {
    key: (title, description) for key, title, description in KB_CATEGORIES
}
KB_CATEGORY_ORDER = {key: index for index, (key, _, _) in enumerate(KB_CATEGORIES)}
PLATFORM_DESTINATION_PREFIXES = {
    ".agent": "antigravity",
    ".agents": "codex",
    ".claude": "claude",
    ".codebuddy": "codebuddy",
    ".codex": "codex",
    ".cursor": "cursor",
    ".devin": "devin",
    ".factory": "factory",
    ".gemini": "gemini",
    ".github": "github",
    ".kiro": "kiro",
    ".kilocode": "kilo",
    ".opencode": "opencode",
    ".pi": "pi",
    ".qoder": "qoder",
    ".reasonix": "reasonix",
    ".trae": "trae",
    ".zcode": "zcode",
}


def source_category(path: Path) -> str:
    parts = path.parts
    upper_name = path.name.upper()
    if is_repo_map(path):
        return "repository-maps"
    if parts and parts[0] in PLATFORM_GUIDANCE_ROOTS:
        return "agent-platform-guidance"
    if path == Path("AGENTS.md"):
        return "agent-platform-guidance"
    if path == Path("README.md"):
        return "repository-overview"
    if path == Path("docs/SD_AI_COMMAND_PACK.md"):
        return "pack-documentation"
    if any(
        upper_name.startswith(prefix)
        for prefix in (
            "ARCHITECTURE",
            "DECISION",
            "DECISIONS",
            "HANDOFF",
            "ROADMAP",
        )
    ):
        return "architecture-decisions"
    if path in {
        Path(".trellis/config.yaml"),
        Path(".trellis/config.yml"),
        Path(".trellis/workflow.md"),
    }:
        return "trellis-workflow-config"
    if len(parts) >= 3 and parts[0] == ".trellis" and parts[1] == "tasks":
        return "task-documentation"
    if len(parts) >= 3 and parts[0] == ".trellis" and parts[1] == "spec":
        if parts[2] == "backend":
            return "trellis-backend-specs"
        if parts[2] == "frontend":
            return "trellis-frontend-specs"
        if parts[2] == "guides":
            return "trellis-thinking-guides"
        return "other-documentation"
    if is_project_manifest(path):
        return "project-manifests"
    if "README" in upper_name and path.parent != Path("."):
        return "package-documentation"
    if parts[:1] == ("docs",):
        return "other-documentation"
    return "other-documentation"


def source_category_sort_key(category: str) -> tuple[int, str]:
    return (KB_CATEGORY_ORDER.get(category, len(KB_CATEGORY_ORDER)), category)


def visible_path_component(value: str) -> str:
    cleaned = value.lstrip(".")
    cleaned = cleaned.replace("trellis-", "").replace("trellis_", "")
    chars = [
        character if character.isalnum() or character in {"-", "_", " "} else "-"
        for character in cleaned
    ]
    return "".join(chars).strip("-_ ") or "document"


def category_folder_for_source(source: Path) -> str:
    title, _ = KB_CATEGORY_BY_KEY.get(
        source_category(source),
        KB_CATEGORY_BY_KEY["other-documentation"],
    )
    return title


def destination_filename_for_source(source: Path) -> str:
    parts = source.parts
    suffix = source.suffix
    if parts and parts[0] in PLATFORM_DESTINATION_PREFIXES:
        prefix = PLATFORM_DESTINATION_PREFIXES[parts[0]]
        stem = source.stem
        if source.name == "SKILL.md" and len(parts) >= 2:
            return f"{prefix}-{visible_path_component(parts[-2])}.md"
        if source.name.lower() == "agents.md":
            return f"{prefix}-agents.md"
        if source.name == "package.json":
            return f"{prefix}-package.json"
        return f"{prefix}-{visible_path_component(stem)}{suffix}"

    if len(parts) >= 3 and parts[0] == ".trellis" and parts[1] == "tasks":
        task_parts = [
            visible_path_component(part)
            for part in parts[2:-1]
            if visible_path_component(part)
        ]
        prefix = "-".join(task_parts)
        stem = visible_path_component(source.stem)
        return f"{prefix}-{stem}{suffix}" if prefix else f"{stem}{suffix}"

    if source_category(source) == "package-documentation":
        parent_parts = [
            visible_path_component(part)
            for part in source.parent.parts
            if visible_path_component(part)
        ]
        prefix = "-".join(parent_parts)
        return f"{prefix}-{source.name}" if prefix else source.name

    return visible_path_component(source.stem) + suffix


def kb_destination_for_source(source: Path) -> Path:
    return Path(category_folder_for_source(source)) / destination_filename_for_source(source)


def source_destination_entries(sources: list[Path]) -> list[tuple[Path, Path]]:
    used: set[Path] = set()
    entries: list[tuple[Path, Path]] = []
    for source in sources:
        destination = kb_destination_for_source(source)
        candidate = destination
        counter = 2
        while candidate in used:
            candidate = destination.with_name(
                f"{destination.stem}-{counter}{destination.suffix}"
            )
            counter += 1
        used.add(candidate)
        entries.append((source, candidate))
    return entries


def source_destination_map(sources: list[Path]) -> dict[Path, Path]:
    return dict(source_destination_entries(sources))


def append_source_sections(
    lines: list[str],
    sources: list[Path],
    *,
    heading_prefix: str = "##",
    include_descriptions: bool = False,
) -> None:
    grouped: dict[str, list[tuple[Path, Path]]] = {}
    for source, destination in source_destination_entries(sources):
        grouped.setdefault(source_category(source), []).append((source, destination))

    for index, category in enumerate(sorted(grouped, key=source_category_sort_key)):
        if index:
            lines.append("")
        title, description = KB_CATEGORY_BY_KEY.get(
            category,
            KB_CATEGORY_BY_KEY["other-documentation"],
        )
        lines.append(f"{heading_prefix} {title}")
        lines.append("")
        lines.append(description)
        lines.append("")
        for source, destination in grouped[category]:
            item = (
                f"[{markdown_path_link_text(destination)}]"
                f"({markdown_link_target(destination)})"
            )
            if include_descriptions:
                item = f"{item} - {source_description(source)}"
            lines.append(f"- {item}")


def dashboard_content(root: Path, sources: list[Path]) -> str:
    github_url = github_repository_url(root)
    dashboard_name = dashboard_path(root).stem
    overview = overview_path(root)
    lines = [
        f"# {dashboard_name}",
        "",
        DASHBOARD_MARKER,
        "",
        "Generated by `scripts/sd-ai-command-pack-update-spec-kb.py`.",
        f"Repository: `{root.name}`",
        (
            f"GitHub: [{github_link_text(github_url)}]({github_url})"
            if github_url
            else "GitHub: not detected from `origin`"
        ),
        (
            f"LLM KB overview: [{markdown_link_text(overview)}]"
            f"({markdown_link_target(overview)})"
        ),
        "",
        "This folder is a self-contained copy of selected repository knowledge "
        "files for Obsidian and LLM indexing.",
        "",
    ]

    if not sources:
        lines.extend(
            [
                "No repository knowledge documents were found.",
                "",
            ]
        )
        return "\n".join(lines)

    append_source_sections(lines, sources, include_descriptions=True)

    lines.append("")
    return "\n".join(lines)


def overview_content(root: Path, sources: list[Path]) -> str:
    github_url = github_repository_url(root)
    lines = [
        "# LLM Knowledge Base",
        "",
        OVERVIEW_MARKER,
        "",
        f"Repository: `{root.name}`",
        (
            f"GitHub: [{github_link_text(github_url)}]({github_url})"
            if github_url
            else "GitHub: not detected from `origin`"
        ),
        f"Copied knowledge files: {len(sources)}",
        "",
        "This folder is generated as a portable, self-contained knowledge base "
        "for LLM and Obsidian indexing. Links in this file point to copied "
        "documents inside this folder; source documents remain in their normal "
        "repository locations.",
        "",
        "## Start Here",
        "",
    ]

    preferred = [
        Path("README.md"),
        Path("AGENTS.md"),
        Path("docs/SD_AI_COMMAND_PACK.md"),
        Path(".trellis/workflow.md"),
        Path(".trellis/config.yaml"),
    ]
    destinations = source_destination_map(sources)
    for source in preferred:
        if source in destinations:
            destination = destinations[source]
            lines.append(
                f"- [{markdown_link_text(destination)}]({markdown_link_target(destination)})"
            )
    if not any(source in destinations for source in preferred):
        lines.append("- No preferred entrypoint documents were found.")

    lines.extend(
        [
            "",
            "## Scope",
            "",
            "- Includes repository documentation, agent instructions, workflow "
            "and spec context, repo-map artifacts, and project manifests.",
            "- Excludes secrets, caches, dependency/vendor folders, build output, "
            "runtime source trees, `.git/`, and `.trellis/workspace/`.",
            "",
        ]
    )

    if sources:
        lines.append("## Contents")
        lines.append("")
        append_source_sections(lines, sources, heading_prefix="###")
    else:
        lines.append("## Contents")
        lines.append("")
        lines.append("No repository knowledge documents were found.")

    lines.append("")
    return "\n".join(lines)


def write_generated_markdown(
    kb_root: Path,
    relative_path: Path,
    content: str,
    marker: str,
) -> tuple[str, str | None]:
    destination = kb_root / relative_path
    if destination.is_symlink():
        return "conflict", f"{relative_path.as_posix()} is a symlink"
    if destination.exists() and not destination.is_file():
        return "conflict", f"{relative_path.as_posix()} is not a file"
    current = read_text_if_present(destination)
    if current is not None:
        if current == content:
            return "present", None
        if marker not in current:
            return (
                "conflict",
                f"{relative_path.as_posix()} exists and is not generated by this tool",
            )
        destination.write_text(content, encoding="utf-8", errors="surrogateescape")
        return "updated", None

    destination.write_text(content, encoding="utf-8", errors="surrogateescape")
    return "created", None


def planned_generated_markdown_state(
    kb_root: Path,
    relative_path: Path,
    content: str,
    marker: str,
) -> tuple[str, str | None]:
    destination = kb_root / relative_path
    if destination.is_symlink():
        return "conflict", f"{relative_path.as_posix()} is a symlink"
    if destination.exists() and not destination.is_file():
        return "conflict", f"{relative_path.as_posix()} is not a file"
    current = read_text_if_present(destination)
    if current is not None:
        if current == content:
            return "present", None
        if marker not in current:
            return (
                "conflict",
                f"{relative_path.as_posix()} exists and is not generated by this tool",
            )
        return "updated", None

    return "created", None


def write_dashboard(
    kb_root: Path,
    root: Path,
    sources: list[Path],
) -> tuple[str, str | None]:
    return write_generated_markdown(
        kb_root,
        dashboard_path(root),
        dashboard_content(root, sources),
        DASHBOARD_MARKER,
    )


def write_overview(
    kb_root: Path,
    root: Path,
    sources: list[Path],
) -> tuple[str, str | None]:
    return write_generated_markdown(
        kb_root,
        overview_path(root),
        overview_content(root, sources),
        OVERVIEW_MARKER,
    )


def planned_dashboard_state(
    kb_root: Path,
    root: Path,
    sources: list[Path],
) -> tuple[str, str | None]:
    return planned_generated_markdown_state(
        kb_root,
        dashboard_path(root),
        dashboard_content(root, sources),
        DASHBOARD_MARKER,
    )


def planned_overview_state(
    kb_root: Path,
    root: Path,
    sources: list[Path],
) -> tuple[str, str | None]:
    return planned_generated_markdown_state(
        kb_root,
        overview_path(root),
        overview_content(root, sources),
        OVERVIEW_MARKER,
    )


def create_copies(root: Path, sources: list[Path]) -> tuple[int, int, list[str]]:
    kb_root = root / KB_DIR
    kb_root.mkdir(parents=True, exist_ok=True)
    entries = source_destination_entries(sources)
    wanted = {destination for _, destination in entries}
    legacy_sources = {source for source, _ in entries}
    removed = prune_stale_kb_entries(kb_root, wanted, root, legacy_sources)
    copied = 0
    conflicts: list[str] = []

    for relative_source, relative_destination in entries:
        source = root / relative_source
        copy = kb_root / relative_destination
        copy.parent.mkdir(parents=True, exist_ok=True)

        if copy.is_symlink():
            current = os.readlink(copy)
            # Only replace links that look tool-created: a relative target
            # resolving back inside the repo. Anything else (absolute target, or
            # a link escaping the repo) is treated as a user-owned conflict and
            # left untouched rather than silently destroyed.
            if os.path.isabs(current) or not is_within(copy.parent / current, root):
                conflicts.append(relative_destination.as_posix())
                continue
            copy.unlink()
        elif copy.exists() and not copy.is_file():
            conflicts.append(relative_destination.as_posix())
            continue
        elif copy.exists() and filecmp.cmp(source, copy, shallow=False):
            copied += 1
            continue

        # Sources are filtered to regular non-symlink files during discovery;
        # follow_symlinks=False keeps that contract explicit so a future
        # symlinked source cannot smuggle out-of-repo content into the KB.
        shutil.copy2(source, copy, follow_symlinks=False)
        copied += 1

    return copied, removed, conflicts


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build or verify the repo-local .obsidian-kb copy folder used by "
            "the SD AI command pack update-spec workflow."
        )
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="report the planned refresh without writing files",
    )
    mode.add_argument(
        "--check",
        action="store_true",
        help="verify the generated folder and ignore entry are current without writing files",
    )
    return parser.parse_args(argv)


def report_kb_state(
    *,
    root: Path,
    mode: str | None,
    gitignore_state: str,
    copies: int,
    stale: int,
    dashboard_state: str,
    conflicts: list[str],
    overview_state: str | None = None,
    legacy_symlinks: int | None = None,
) -> None:
    print(f"Obsidian KB: {KB_DIR}")
    if mode is not None:
        print(f"mode: {mode}")
    print(f"gitignore: {gitignore_state}")
    print(f"copies: {copies}")
    print(f"stale generated entries removed: {stale}")
    if legacy_symlinks is not None:
        symlink_label = (
            "legacy symlinks to convert"
            if mode in {"dry-run", "check"}
            else "legacy symlinks converted"
        )
        print(f"{symlink_label}: {legacy_symlinks}")
    print(f"dashboard: {dashboard_state}")
    if overview_state is not None:
        print(f"llm overview: {overview_state}")
    if conflicts:
        print("conflicts:")
        for conflict in conflicts:
            print(f"  - {conflict}")
    else:
        print("conflicts: none")
    source = shlex.quote(str(root / KB_DIR) + "/.")
    target = shlex.quote(f"/absolute/path/to/vault/{root.name}-KB")
    print("vault copy example: " f"cp -R {source} {target}")


def dry_run(root: Path) -> int:
    sources = discover_sources(root)
    present, stale, copy_issues = collect_copy_state(root, sources)
    legacy_symlinks = legacy_generated_symlink_count(root)
    dashboard_state, dashboard_conflict = planned_dashboard_state(
        root / KB_DIR,
        root,
        sources,
    )
    overview_state, overview_conflict = planned_overview_state(
        root / KB_DIR,
        root,
        sources,
    )
    conflicts = [
        issue
        for issue in copy_issues
        if not issue.endswith(" is missing")
        and not issue.endswith(" is not current")
        and not issue.endswith(" is a legacy generated symlink")
    ]
    if dashboard_conflict is not None:
        conflicts.append(dashboard_conflict)
    if overview_conflict is not None:
        conflicts.append(overview_conflict)

    report_kb_state(
        root=root,
        mode="dry-run",
        gitignore_state=f"would be {planned_gitignore_state(root)}",
        copies=present,
        stale=stale,
        legacy_symlinks=legacy_symlinks,
        dashboard_state=f"would be {dashboard_state}",
        overview_state=f"would be {overview_state}",
        conflicts=conflicts,
    )
    print(f"planned copies: {len(sources)}")
    return 0


def check_current(root: Path) -> int:
    sources = discover_sources(root)
    present, stale, copy_issues = collect_copy_state(root, sources)
    legacy_symlinks = legacy_generated_symlink_count(root)
    gitignore_state = planned_gitignore_state(root)
    dashboard_state, dashboard_conflict = planned_dashboard_state(
        root / KB_DIR,
        root,
        sources,
    )
    overview_state, overview_conflict = planned_overview_state(
        root / KB_DIR,
        root,
        sources,
    )

    conflicts = list(copy_issues)
    if dashboard_conflict is not None:
        conflicts.append(dashboard_conflict)
    if overview_conflict is not None:
        conflicts.append(overview_conflict)
    if gitignore_state not in {"present", "local-exclude present"}:
        conflicts.append(f"ignore entry is not current: {gitignore_state}")
    if stale:
        conflicts.append(f"{stale} stale generated entries would be removed")
    if dashboard_state != "present":
        conflicts.append(f"dashboard is not current: {dashboard_state}")
    if overview_state != "present":
        conflicts.append(f"llm overview is not current: {overview_state}")

    report_kb_state(
        root=root,
        mode="check",
        gitignore_state=gitignore_state,
        copies=present,
        stale=stale,
        legacy_symlinks=legacy_symlinks,
        dashboard_state=dashboard_state,
        overview_state=overview_state,
        conflicts=conflicts,
    )
    print(f"expected copies: {len(sources)}")
    return 1 if conflicts else 0


def refresh(root: Path) -> int:
    try:
        gitignore_state = ensure_gitignore(root)
        sources = discover_sources(root)
        legacy_symlinks = legacy_generated_symlink_count(root)
        copied, removed, conflicts = create_copies(root, sources)
        dashboard_state, dashboard_conflict = write_dashboard(
            root / KB_DIR,
            root,
            sources,
        )
        overview_state, overview_conflict = write_overview(
            root / KB_DIR,
            root,
            sources,
        )
        if dashboard_conflict is not None:
            conflicts.append(dashboard_conflict)
        if overview_conflict is not None:
            conflicts.append(overview_conflict)
    except OSError as error:
        print(f"error: failed to refresh {KB_DIR}: {error}", file=sys.stderr)
        return 2

    report_kb_state(
        root=root,
        mode=None,
        gitignore_state=gitignore_state,
        copies=copied,
        stale=removed,
        legacy_symlinks=legacy_symlinks,
        dashboard_state=dashboard_state,
        overview_state=overview_state,
        conflicts=conflicts,
    )
    # Exit 3: refresh completed but some entries could not be brought
    # current (user-owned symlinks, occupied non-files, dashboard
    # collisions). Automation must not read a partially-stale KB as
    # success; 1 stays the --check staleness exit and 2 stays hard errors.
    return 3 if conflicts else 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = repo_root()

    try:
        if args.dry_run:
            return dry_run(root)
        if args.check:
            return check_current(root)
        return refresh(root)
    except OSError as error:
        print(f"error: failed to inspect {KB_DIR}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
