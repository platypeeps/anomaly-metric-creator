#!/usr/bin/env python3
"""Validate registry-derived shipped-surface closure without mutating the repo."""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
from collections import Counter, deque
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import ModuleType
from typing import Any, Iterable, Mapping, Sequence

SCHEMA_VERSION = 1
SUPPORTED_MANIFEST_SCHEMA = 1
MAX_AUTHORITATIVE_BYTES = 1024 * 1024
MAX_CHANGED_PATH_BYTES = 4 * 1024 * 1024
MAX_CHANGED_PATHS = 20_000
MAX_GRAPH_ITEMS = 20_000
MAX_FINDINGS = 200
HELP_CATALOG = "templates/.agents/skills/sd-help/references/command-catalog.md"
SURFACE_HELPER = "scripts/sd-ai-command-pack-surface-check.py"
CHECK_CONFIG = ".sd-ai-command-pack/check.json"
FULL_CHECK = "templates/scripts/sd-ai-command-pack-full-check.sh"
CI_WORKFLOW = ".github/workflows/tests.yml"
RELEASE_EVIDENCE = (
    "manifest.json",
    "CHANGELOG.md",
    ".sd-ai-command-pack/manifest.json",
    "docs/fleet/candidate-validation.json",
)


class SurfaceInputError(RuntimeError):
    """A controlled invalid or unreadable authoritative input."""


@dataclass(frozen=True, order=True)
class Finding:
    code: str
    path: str
    relation: str
    message: str
    owner_command: str


@dataclass(frozen=True, order=True)
class Node:
    id: str
    kind: str
    path: str | None


@dataclass(frozen=True, order=True)
class Edge:
    source: str
    relation: str
    target: str


def _controlled(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _safe_relative(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or _controlled(value):
        raise SurfaceInputError(f"{field} must be non-empty control-free text")
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or ".." in posix.parts
        or "\\" in value
    ):
        raise SurfaceInputError(f"{field} is an unsafe repository path: {value!r}")
    return posix.as_posix()


def _regular_bytes(root: Path, relative: str, *, label: str) -> bytes:
    path = root / relative
    try:
        metadata = path.lstat()
    except FileNotFoundError as error:
        raise SurfaceInputError(f"missing {label}: {relative}") from error
    except OSError as error:
        raise SurfaceInputError(f"cannot inspect {label} {relative}: {error}") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise SurfaceInputError(f"{label} must be a regular non-symlink file: {relative}")
    if metadata.st_size > MAX_AUTHORITATIVE_BYTES:
        raise SurfaceInputError(
            f"{label} exceeds {MAX_AUTHORITATIVE_BYTES} bytes: {relative}"
        )
    try:
        return path.read_bytes()
    except OSError as error:
        raise SurfaceInputError(f"cannot read {label} {relative}: {error}") from error


def _regular_text(root: Path, relative: str, *, label: str) -> str:
    try:
        return _regular_bytes(root, relative, label=label).decode("utf-8", "strict")
    except UnicodeDecodeError as error:
        raise SurfaceInputError(f"{label} must be valid UTF-8: {relative}") from error


def _load_json(root: Path, relative: str, *, label: str) -> tuple[str, Any]:
    text = _regular_text(root, relative, label=label)
    try:
        return text, json.loads(text)
    except json.JSONDecodeError as error:
        raise SurfaceInputError(
            f"{label} is invalid JSON at line {error.lineno} column {error.colno}: {relative}"
        ) from error


def _run_git(root: Path, args: Sequence[str], *, optional: bool = False) -> bytes | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        if optional:
            return None
        raise SurfaceInputError(f"git {' '.join(args)} could not run: {error}") from error
    if result.returncode != 0:
        if optional:
            return None
        detail = (result.stderr or result.stdout).decode("utf-8", "replace").strip()
        raise SurfaceInputError(f"git {' '.join(args)} failed: {detail or result.returncode}")
    return result.stdout


def _decode_git_paths(raw: bytes, *, context: str) -> set[str]:
    if len(raw) > MAX_CHANGED_PATH_BYTES:
        raise SurfaceInputError(
            f"{context} exceeds {MAX_CHANGED_PATH_BYTES} bytes"
        )
    values = [value for value in raw.split(b"\0") if value]
    if len(values) > MAX_CHANGED_PATHS:
        raise SurfaceInputError(f"{context} exceeds {MAX_CHANGED_PATHS} paths")
    paths: set[str] = set()
    for raw_path in values:
        try:
            value = raw_path.decode("utf-8", "strict")
        except UnicodeDecodeError as error:
            raise SurfaceInputError(f"{context} contains a non-UTF-8 path") from error
        paths.add(_safe_relative(value, field=context))
    return paths


def _resolves(root: Path, ref: str) -> bool:
    return _run_git(
        root, ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], optional=True
    ) is not None


def _default_base_ref(root: Path) -> str | None:
    configured = os.environ.get("SD_AI_COMMAND_PACK_FULL_CHECK_RELEASE_BASE_REF", "").strip()
    if configured:
        if not _resolves(root, configured):
            raise SurfaceInputError(f"configured base ref does not resolve: {configured}")
        return configured
    for candidate in ("origin/main", "main"):
        if _resolves(root, candidate):
            return candidate
    return None


def collect_changed_paths(root: Path, base_ref: str | None) -> tuple[str | None, tuple[str, ...]]:
    """Collect committed, staged, unstaged, and non-ignored untracked paths."""

    resolved_base = base_ref or _default_base_ref(root)
    chunks: list[bytes] = []
    if resolved_base:
        if not _resolves(root, resolved_base):
            raise SurfaceInputError(f"base ref does not resolve: {resolved_base}")
        chunks.append(
            _run_git(root, ["diff", "--name-only", "-z", f"{resolved_base}...HEAD"])
            or b""
        )
    for args in (
        ["diff", "--cached", "--name-only", "-z"],
        ["diff", "--name-only", "-z"],
        ["ls-files", "-z", "--others", "--exclude-standard"],
    ):
        chunks.append(_run_git(root, args) or b"")
    paths = _decode_git_paths(b"\0".join(chunks), context="changed-path inventory")
    return resolved_base, tuple(sorted(paths))


def _tracked_template_paths(root: Path) -> set[str]:
    raw = _run_git(
        root,
        ["ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "templates"],
    )
    return _decode_git_paths(raw or b"", context="template-source inventory")


def _load_source_module(root: Path, relative: str, name: str) -> ModuleType:
    path = root / relative
    if not path.is_file() or path.is_symlink():
        raise SurfaceInputError(f"missing source validator module: {relative}")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SurfaceInputError(f"cannot load source validator module: {relative}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except (ImportError, RuntimeError, SystemExit, TypeError, ValueError) as error:
        raise SurfaceInputError(f"cannot load {relative}: {error}") from error
    return module


def _registry_module(root: Path) -> ModuleType:
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    try:
        return importlib.import_module("installer.registry")
    except (ImportError, RuntimeError, TypeError, ValueError) as error:
        raise SurfaceInputError(f"cannot load installer registry: {error}") from error


def _manifest_entries(root: Path, registry: ModuleType) -> tuple[str, list[dict[str, str]]]:
    text, raw = _load_json(root, "manifest.json", label="manifest")
    if not isinstance(raw, dict):
        raise SurfaceInputError("manifest must be a JSON object")
    version = raw.get("schemaVersion", 1)
    if isinstance(version, bool) or not isinstance(version, int):
        raise SurfaceInputError("manifest schemaVersion must be an integer")
    if version != SUPPORTED_MANIFEST_SCHEMA:
        raise SurfaceInputError(f"unsupported manifest schemaVersion: {version!r}")
    files = raw.get("files")
    if not isinstance(files, list) or len(files) > MAX_GRAPH_ITEMS:
        raise SurfaceInputError("manifest files must be a bounded array")
    known_platforms = set(registry.PLATFORM_REGISTRY)
    known_platforms.add("shared")
    known_kinds = {
        "command", "config", "doc", "managed-block", "prompt", "script", "skill", "workflow"
    }
    entries: list[dict[str, str]] = []
    seen_targets: dict[str, str] = {}
    seen_relations: set[tuple[str, str]] = set()
    for index, item in enumerate(files):
        field = f"manifest.files[{index}]"
        if not isinstance(item, dict):
            raise SurfaceInputError(f"{field} must be an object")
        normalized: dict[str, str] = {}
        for key in ("platform", "kind", "source", "target"):
            value = item.get(key)
            if not isinstance(value, str) or not value or _controlled(value):
                raise SurfaceInputError(f"{field}.{key} must be non-empty control-free text")
            normalized[key] = value
        for key in ("anchor", "install"):
            value = item.get(key)
            if value is not None and not isinstance(value, str):
                raise SurfaceInputError(f"{field}.{key} must be text when present")
            if isinstance(value, str):
                normalized[key] = value
        if normalized["platform"] not in known_platforms:
            raise SurfaceInputError(f"{field}.platform is unknown: {normalized['platform']}")
        if normalized["kind"] not in known_kinds:
            raise SurfaceInputError(f"{field}.kind is unknown: {normalized['kind']}")
        source = _safe_relative(normalized["source"], field=f"{field}.source")
        target = _safe_relative(normalized["target"], field=f"{field}.target")
        if not source.startswith("templates/"):
            raise SurfaceInputError(f"{field}.source must be rooted under templates/: {source}")
        if "anchor" in normalized:
            normalized["anchor"] = _safe_relative(
                normalized["anchor"], field=f"{field}.anchor"
            )
        relation = (source, target)
        if relation in seen_relations:
            raise SurfaceInputError(f"duplicate manifest relation: {source} -> {target}")
        folded = target.casefold()
        if folded in seen_targets:
            raise SurfaceInputError(
                f"duplicate manifest target: {target} collides with {seen_targets[folded]}"
            )
        seen_relations.add(relation)
        seen_targets[folded] = target
        normalized["source"] = source
        normalized["target"] = target
        entries.append(normalized)
    return text, entries


def _source_only_paths(registry: ModuleType, linter: ModuleType) -> set[str]:
    commands = {command.name: command for command in registry.COMMAND_REGISTRY}
    paths: set[str] = set()
    for name in registry.SOURCE_ONLY_COMMAND_NAMES:
        command = commands[name]
        paths.update(
            path for path in linter._required_source_paths(command) if path.startswith("templates/")
        )
        for reference in registry.SOURCE_ONLY_SKILL_REFERENCES.get(name, ()):
            paths.add(f"templates/.agents/skills/{name}/{reference}")
    return paths


def _node_kind(path: str, source_only: set[str]) -> str:
    if path in source_only:
        return "source-only"
    if path in RELEASE_EVIDENCE:
        return "provenance"
    if path in {FULL_CHECK, f"templates/{SURFACE_HELPER}"}:
        return "check-only"
    if path.startswith("templates/"):
        return "generated"
    if path.startswith("docs/") or path == "README.md":
        return "documentation-only"
    if path.startswith(".github/") or path.startswith("tests/") or path == CHECK_CONFIG:
        return "check-only"
    return "installable"


def _add_node(nodes: dict[str, Node], identifier: str, kind: str, path: str | None) -> str:
    node = Node(identifier, kind, path)
    existing = nodes.get(identifier)
    if existing is not None and existing != node:
        raise SurfaceInputError(f"duplicate graph node id with conflicting types: {identifier}")
    nodes[identifier] = node
    return identifier


def _graph(
    registry: ModuleType,
    entries: Sequence[Mapping[str, str]],
    source_only: set[str],
) -> tuple[dict[str, Node], set[Edge]]:
    nodes: dict[str, Node] = {}
    edges: set[Edge] = set()
    manifest_node = _add_node(nodes, "path:manifest.json", "provenance", "manifest.json")
    for command in registry.COMMAND_REGISTRY:
        registry_id = _add_node(
            nodes, f"command:{command.name}", "registry", "installer/registry.py"
        )
        authored = f".github/command-sources/{command.name}.md"
        authored_id = _add_node(nodes, f"path:{authored}", "check-only", authored)
        edges.add(Edge(registry_id, "defines", authored_id))
        help_id = _add_node(nodes, f"help:{command.name}", "documentation-only", HELP_CATALOG)
        edges.add(Edge(registry_id, "documents", help_id))
    for entry in entries:
        source = entry["source"]
        target = entry["target"]
        source_id = _add_node(nodes, f"path:{source}", _node_kind(source, source_only), source)
        target_kind = (
            "check-only" if target == SURFACE_HELPER else _node_kind(target, source_only)
        )
        target_id = _add_node(nodes, f"path:{target}", target_kind, target)
        edges.add(Edge(source_id, "installs-as", target_id))
        edges.add(Edge(manifest_node, "declares", source_id))
    for source in sorted(source_only):
        source_id = _add_node(nodes, f"path:{source}", "source-only", source)
        edges.add(Edge(source_id, "excluded-from", manifest_node))
    for retirement in registry.RETIRED_COMMAND_SURFACES:
        retired_id = _add_node(
            nodes, f"retired:{retirement.id}", "retired", "installer/registry.py"
        )
        for target in retirement.installed_targets:
            target_id = _add_node(nodes, f"retired-path:{target}", "retired", target)
            edges.add(Edge(retired_id, "retires", target_id))
    for path in RELEASE_EVIDENCE:
        _add_node(nodes, f"path:{path}", "provenance", path)
    for path, kind in (
        (CHECK_CONFIG, "check-only"),
        (FULL_CHECK, "check-only"),
        (CI_WORKFLOW, "check-only"),
        (SURFACE_HELPER, "check-only"),
    ):
        _add_node(nodes, f"path:{path}", kind, path)
    edges.add(Edge(f"path:{CHECK_CONFIG}", "checks-with", f"path:{SURFACE_HELPER}"))
    edges.add(Edge(f"path:{FULL_CHECK}", "checks-with", f"path:{SURFACE_HELPER}"))
    edges.add(Edge(f"path:{CI_WORKFLOW}", "invokes", f"path:{FULL_CHECK}"))
    if len(nodes) > MAX_GRAPH_ITEMS or len(edges) > MAX_GRAPH_ITEMS:
        raise SurfaceInputError("surface graph exceeds the bounded node or edge limit")
    return nodes, edges


def _closure(
    nodes: Mapping[str, Node], edges: Iterable[Edge], changed_paths: Sequence[str]
) -> tuple[list[Node], list[Edge]]:
    adjacency: dict[str, set[str]] = {}
    edge_list = sorted(set(edges))
    for edge in edge_list:
        adjacency.setdefault(edge.source, set()).add(edge.target)
        adjacency.setdefault(edge.target, set()).add(edge.source)
    selected = {
        node.id for node in nodes.values() if node.path is not None and node.path in changed_paths
    }
    queue = deque(sorted(selected))
    while queue:
        current = queue.popleft()
        for neighbor in sorted(adjacency.get(current, ())):
            if neighbor not in selected:
                selected.add(neighbor)
                queue.append(neighbor)
    return (
        sorted(node for identifier, node in nodes.items() if identifier in selected),
        [edge for edge in edge_list if edge.source in selected and edge.target in selected],
    )


def _caller_findings(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    _, raw_config = _load_json(root, CHECK_CONFIG, label="sd-check configuration")
    if not isinstance(raw_config, dict) or raw_config.get("schemaVersion") != 1:
        raise SurfaceInputError("sd-check configuration must use schemaVersion 1")
    checks = raw_config.get("checks")
    if not isinstance(checks, list):
        raise SurfaceInputError("sd-check configuration checks must be an array")
    registered = 0
    for index, check in enumerate(checks):
        if not isinstance(check, dict):
            raise SurfaceInputError(f"sd-check configuration checks[{index}] must be an object")
        argv = check.get("argv")
        if not isinstance(argv, list) or any(not isinstance(value, str) for value in argv):
            raise SurfaceInputError(f"sd-check configuration checks[{index}].argv must be a text array")
        registered += sum(value == SURFACE_HELPER for value in argv)
    if registered != 1:
        findings.append(
            Finding(
                "checker.registration",
                CHECK_CONFIG,
                "checks-with",
                f"sd-check must register {SURFACE_HELPER} exactly once; found {registered}",
                f"edit {CHECK_CONFIG}",
            )
        )
    full_check = _regular_text(root, FULL_CHECK, label="local pre-publication gate")
    local_count = full_check.count(PurePosixPath(SURFACE_HELPER).name)
    if local_count != 1:
        findings.append(
            Finding(
                "checker.registration",
                FULL_CHECK,
                "checks-with",
                f"local pre-publication must invoke the surface helper exactly once; found {local_count}",
                f"edit {FULL_CHECK}",
            )
        )
    workflow = _regular_text(root, CI_WORKFLOW, label="CI workflow")
    ci_count = workflow.count("run_pack_source_drift_gates")
    if ci_count != 1:
        findings.append(
            Finding(
                "checker.registration",
                CI_WORKFLOW,
                "invokes",
                f"CI must invoke the shared source-drift gate exactly once; found {ci_count}",
                f"edit {CI_WORKFLOW}",
            )
        )
    return findings


def _generator_finding(root: Path) -> Finding | None:
    generator = ".github/scripts/generate-command-surfaces.py"
    _regular_text(root, generator, label="command-surface generator")
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        result = subprocess.run(
            [sys.executable, generator, "--check"],
            cwd=root,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=120,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError) as error:
        raise SurfaceInputError(f"generated-surface check could not run: {error}") from error
    if result.returncode == 0:
        return None
    detail = " ".join(result.stdout.split())[:800]
    return Finding(
        "generated.stale",
        generator,
        "generates",
        detail or f"generator check exited {result.returncode}",
        "make generate",
    )


def _release_evidence_findings(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for relative in RELEASE_EVIDENCE[1:]:
        try:
            _regular_text(root, relative, label="release evidence")
        except SurfaceInputError as error:
            findings.append(
                Finding(
                    "provenance.invalid",
                    relative,
                    "requires-release-evidence",
                    str(error),
                    "restore release evidence, then run make sync and the fleet candidate check",
                )
            )
    receipt = root / ".sd-ai-command-pack/manifest.json"
    if receipt.is_file() and not receipt.is_symlink():
        if receipt.read_bytes() != (root / "manifest.json").read_bytes():
            findings.append(
                Finding(
                    "provenance.stale",
                    ".sd-ai-command-pack/manifest.json",
                    "mirrors",
                    "installed pack manifest differs from the release manifest",
                    "make sync",
                )
            )
    candidate = root / "scripts/sd-ai-command-pack-fleet-candidate-check.py"
    if candidate.is_file() and not candidate.is_symlink():
        environment = dict(os.environ)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        try:
            result = subprocess.run(
                [sys.executable, str(candidate), "--check-ledger"],
                cwd=root,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="strict",
                timeout=60,
                env=environment,
            )
        except (OSError, subprocess.TimeoutExpired, UnicodeError) as error:
            raise SurfaceInputError(f"candidate-ledger check could not run: {error}") from error
        if result.returncode != 0:
            detail = " ".join(result.stdout.split())[:800]
            findings.append(
                Finding(
                    "provenance.candidate-stale",
                    "docs/fleet/candidate-validation.json",
                    "requires-release-evidence",
                    detail or f"candidate-ledger check exited {result.returncode}",
                    "python3 scripts/sd-ai-command-pack-fleet-candidate-check.py",
                )
            )
    return findings


def _evaluate(root: Path, base_ref: str | None) -> dict[str, object]:
    registry = _registry_module(root)
    linter = _load_source_module(
        root, ".github/scripts/check-command-surface-drift.py", "sd_surface_linter"
    )
    _manifest_text, entries = _manifest_entries(root, registry)
    source_only = _source_only_paths(registry, linter)
    manifest_sources = {entry["source"] for entry in entries}
    tracked_templates = _tracked_template_paths(root)
    findings: list[Finding] = []

    for path in sorted(tracked_templates - manifest_sources - source_only):
        findings.append(
            Finding(
                "source.unregistered-template",
                path,
                "declares",
                "template source is absent from both manifest and explicit source-only registration",
                "add a manifest entry or declare SOURCE_ONLY_SKILL_REFERENCES",
            )
        )
    for path in sorted((manifest_sources | source_only) - tracked_templates):
        findings.append(
            Finding(
                "source.missing",
                path,
                "declares",
                "declared template source is not tracked or intended as a non-ignored untracked file",
                "restore the file or remove its authoritative registration",
            )
        )
    for path in sorted(manifest_sources | source_only):
        try:
            _regular_text(root, path, label="declared template source")
        except SurfaceInputError as error:
            findings.append(
                Finding(
                    "source.invalid",
                    path,
                    "declares",
                    str(error),
                    "restore a bounded regular UTF-8 source file",
                )
            )

    lint_report = linter.lint_repository(root)
    for item in lint_report.findings:
        findings.append(
            Finding(
                f"command.{item.category}",
                item.path,
                "validates",
                f"{item.identifier}: {item.message}",
                item.suggestion,
            )
        )
    findings.extend(_caller_findings(root))
    findings.extend(_release_evidence_findings(root))
    generated = _generator_finding(root)
    if generated is not None:
        findings.append(generated)

    for entry in entries:
        source = entry["source"]
        target = entry["target"]
        target_path = root / target
        if entry["kind"] == "managed-block" or not target_path.exists():
            continue
        try:
            target_bytes = _regular_bytes(root, target, label="generated mirror")
            source_bytes = _regular_bytes(root, source, label="template source")
        except SurfaceInputError as error:
            findings.append(
                Finding(
                    "mirror.invalid",
                    target,
                    "mirrors",
                    str(error),
                    "make sync",
                )
            )
            continue
        if target_bytes != source_bytes:
            findings.append(
                Finding(
                    "mirror.stale",
                    target,
                    "mirrors",
                    f"generated mirror differs from {source}",
                    "make sync",
                )
            )

    resolved_base, changed_paths = collect_changed_paths(root, base_ref)
    nodes, edges = _graph(registry, entries, source_only)
    affected_nodes, affected_edges = _closure(nodes, edges, changed_paths)
    unique_findings = sorted(set(findings))
    truncated = len(unique_findings) > MAX_FINDINGS
    visible_findings = unique_findings[:MAX_FINDINGS]
    counts = Counter(finding.code for finding in unique_findings)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "status": "failed" if unique_findings else "clean",
        "baseRef": resolved_base,
        "changedPaths": list(changed_paths),
        "graph": {
            "nodeCount": len(nodes),
            "edgeCount": len(edges),
            "affectedNodes": [asdict(node) for node in affected_nodes],
            "affectedEdges": [asdict(edge) for edge in affected_edges],
        },
        "findingCounts": dict(sorted(counts.items())),
        "findingCount": len(unique_findings),
        "findingsTruncated": truncated,
        "findings": [
            {
                "code": finding.code,
                "path": finding.path,
                "relation": finding.relation,
                "message": finding.message,
                "ownerCommand": finding.owner_command,
            }
            for finding in visible_findings
        ],
    }


def _render_human(report: Mapping[str, object]) -> str:
    if report.get("status") == "clean":
        graph = report.get("graph")
        changed = report.get("changedPaths")
        affected = graph.get("affectedNodes", []) if isinstance(graph, dict) else []
        return (
            "shipped-surface closure: clean; "
            f"{len(changed) if isinstance(changed, list) else 0} changed path(s), "
            f"{len(affected) if isinstance(affected, list) else 0} affected node(s)"
        )
    lines: list[str] = []
    findings = report.get("findings")
    if isinstance(findings, list):
        for item in findings:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"{item.get('path')}: {item.get('code')}: {item.get('message')}; "
                f"prepare with: {item.get('ownerCommand')}"
            )
    count = report.get("findingCount", len(lines))
    lines.append(f"shipped-surface closure: failed with {count} finding(s)")
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--base-ref")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        root = args.root.resolve(strict=True)
        if not root.is_dir():
            raise SurfaceInputError(f"repository root is not a directory: {root}")
        report = _evaluate(root, args.base_ref)
    except (OSError, SurfaceInputError) as error:
        report = {
            "schemaVersion": SCHEMA_VERSION,
            "status": "invalid",
            "findingCount": 1,
            "findingCounts": {"input.invalid": 1},
            "findingsTruncated": False,
            "findings": [
                {
                    "code": "input.invalid",
                    "path": ".",
                    "relation": "loads",
                    "message": str(error),
                    "ownerCommand": "correct the authoritative input",
                }
            ],
        }
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        output = _render_human(report)
        print(output, file=sys.stderr if report["status"] != "clean" else sys.stdout)
    return 0 if report["status"] == "clean" else 2 if report["status"] == "invalid" else 1


if __name__ == "__main__":
    raise SystemExit(main())
