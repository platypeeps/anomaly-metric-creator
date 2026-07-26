#!/usr/bin/env python3
"""Build a deterministic applicability report for sd-audit-repo charters."""

from __future__ import annotations

import argparse
import heapq
import json
import os
import stat
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TypedDict

from sd_ai_command_pack_lib import CommandError, run_git

SCHEMA_VERSION = 1
ROUTER_VERSION = 1
CHARTER_VERSION = 1
MAX_PATHS = 100_000
MAX_MANIFEST_BYTES = 1_048_576
MAX_EVIDENCE = 20

CHARTERS = (
    "architecture",
    "design",
    "correctness",
    "security",
    "testing",
    "documentation",
    "bloat",
    "performance",
    "dependencies",
    "tooling",
    "release-hygiene",
    "improvements",
    "consumer-impact",
    "observability",
    "accessibility-i18n",
)

MANDATORY_STANDARD = frozenset(
    {
        "correctness",
        "security",
        "testing",
        "tooling",
        "release-hygiene",
    }
)

CONDITIONAL_APPLICABILITY = frozenset(
    {
        "dependencies",
        "documentation",
        "consumer-impact",
        "observability",
        "accessibility-i18n",
    }
)

OPTIONAL_ROUTING = {
    "architecture": (
        "multi-component",
        "infrastructure",
        "data-store",
        "deployment",
    ),
    "design": ("runtime-code", "public-api", "downstream-consumer"),
    "documentation": ("documentation",),
    "bloat": ("large-codebase",),
    "performance": ("deployed-service", "data-store", "batch-runtime"),
    "dependencies": ("dependency-manifest",),
    "improvements": ("improvement-signals",),
    "consumer-impact": ("downstream-consumer", "public-api"),
    "observability": ("deployed-service", "infrastructure"),
    "accessibility-i18n": ("user-interface",),
}

DEPENDENCY_MANIFEST_NAMES = frozenset(
    {
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "bun.lock",
        "bun.lockb",
        "pyproject.toml",
        "requirements.txt",
        "requirements-dev.txt",
        "pipfile",
        "pipfile.lock",
        "poetry.lock",
        "uv.lock",
        "go.mod",
        "go.sum",
        "cargo.toml",
        "cargo.lock",
        "gemfile",
        "gemfile.lock",
        "composer.json",
        "composer.lock",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "packages.lock.json",
        "paket.dependencies",
        "mix.exs",
        "mix.lock",
    }
)

SOURCE_EXTENSIONS = {
    ".c": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".css": "css",
    ".cs": "csharp",
    ".ex": "elixir",
    ".exs": "elixir",
    ".go": "go",
    ".htm": "html",
    ".html": "html",
    ".java": "java",
    ".js": "javascript",
    ".jsx": "javascript",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".php": "php",
    ".py": "python",
    ".rb": "ruby",
    ".rs": "rust",
    ".scss": "scss",
    ".scala": "scala",
    ".sh": "shell",
    ".swift": "swift",
    ".svelte": "svelte",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".vue": "vue",
}

UI_EXTENSIONS = frozenset({".html", ".htm", ".jsx", ".tsx", ".vue", ".svelte"})
DOCUMENTATION_EXTENSIONS = frozenset(
    {"", ".adoc", ".asciidoc", ".markdown", ".md", ".mdx", ".org", ".rst", ".txt"}
)
NON_PRODUCT_ROOTS = frozenset(
    {
        ".agents",
        ".codex",
        ".gemini",
        ".github",
        ".opencode",
        ".trellis",
        "docs",
        "documentation",
        "node_modules",
        "spec",
        "specs",
        "test",
        "tests",
        "templates",
        "vendor",
    }
)
SOURCE_CONTAINER_PARTS = frozenset(
    {"app", "apps", "backend", "client", "frontend", "lib", "packages", "server", "src"}
)
KNOWN_NON_SOURCE_EXTENSIONS = frozenset(
    {
        ".css",
        ".csv",
        ".graphql",
        ".ini",
        ".jpg",
        ".jpeg",
        ".json",
        ".lock",
        ".md",
        ".png",
        ".prisma",
        ".scss",
        ".sql",
        ".svg",
        ".tf",
        ".toml",
        ".txt",
        ".xml",
        ".yaml",
        ".yml",
    }
)
MANIFEST_CONTENT_NAMES = frozenset(
    {
        "package.json",
        "pyproject.toml",
        "requirements.txt",
        "requirements-dev.txt",
        "cargo.toml",
        "go.mod",
        "gemfile",
        "composer.json",
        "pom.xml",
        "mix.exs",
    }
)

UI_DEPENDENCY_TOKENS = (
    "@angular/",
    "@sveltejs/",
    "@vue/",
    "astro",
    "next",
    "nuxt",
    "react",
    "solid-js",
    "svelte",
    "vue",
)
DATASTORE_TOKENS = (
    "diesel",
    "django",
    "mongodb",
    "mongoose",
    "mysql",
    "postgres",
    "prisma",
    "redis",
    "sequelize",
    "sqlalchemy",
    "sqlite",
    "typeorm",
)
SERVICE_TOKENS = (
    "actix-web",
    "axum",
    "django",
    "express",
    "fastapi",
    "flask",
    "gin-gonic",
    "graphql",
    "nestjs",
    "rails",
    "spring-boot",
)


class AuditRoutingError(RuntimeError):
    """Raised when an audit-routing request is invalid."""


class FingerprintEvidenceError(CommandError):
    """Raised when repository evidence is unknown or contradictory."""

    def __init__(self, state: str, message: str) -> None:
        super().__init__(message)
        self.state = state


class FingerprintPayload(TypedDict):
    id: str
    state: str
    evidence: list[str]
    values: list[str]
    reason: str | None


class CharterRow(TypedDict):
    id: str
    charterVersion: int
    classification: str
    status: str
    reasonCode: str
    evidence: list[str]


class AuditReport(TypedDict):
    schemaVersion: int
    routerVersion: int
    repository: str
    mode: str
    requestedDimensions: list[str]
    classificationStatus: str
    fallbackToExhaustive: bool
    fingerprints: list[FingerprintPayload]
    charters: list[CharterRow]
    summary: dict[str, int]
    warnings: list[str]


@dataclass(frozen=True)
class Fingerprint:
    id: str
    state: str
    evidence: tuple[str, ...]
    values: tuple[str, ...] = ()
    reason: str | None = None

    def as_json(self) -> FingerprintPayload:
        return {
            "id": self.id,
            "state": self.state,
            "evidence": list(self.evidence),
            "values": list(self.values),
            "reason": self.reason,
        }


def _bounded(items: Iterable[str]) -> tuple[str, ...]:
    return tuple(heapq.nsmallest(MAX_EVIDENCE, set(items)))


def _path_name(path: str) -> str:
    return PurePosixPath(path).name.casefold()


def _path_parts(path: str) -> tuple[str, ...]:
    return tuple(part.casefold() for part in PurePosixPath(path).parts)


def _has_part(path: str, names: frozenset[str]) -> bool:
    return any(part in names for part in _path_parts(path))


def _suffix(path: str) -> str:
    return PurePosixPath(path).suffix.casefold()


def _is_product_path(path: str) -> bool:
    parts = _path_parts(path)
    if not parts:
        return False
    first = parts[0]
    return first not in NON_PRODUCT_ROOTS and not first.startswith(".")


def _os_error_detail(exc: OSError | UnicodeError) -> str:
    if isinstance(exc, OSError) and exc.errno is not None:
        return f"os-error-{exc.errno}"
    return exc.__class__.__name__


def _open_without_following_symlinks(path: str, flags: int) -> int:
    return os.open(path, flags | getattr(os, "O_NOFOLLOW", 0))


def _fingerprint(
    fingerprint_id: str,
    evidence: Iterable[str],
    *,
    values: Iterable[str] = (),
    absent_reason: str,
) -> Fingerprint:
    bounded_evidence = _bounded(evidence)
    bounded_values = _bounded(values)
    return Fingerprint(
        id=fingerprint_id,
        state="present" if bounded_evidence or bounded_values else "absent",
        evidence=bounded_evidence,
        values=bounded_values,
        reason=None if bounded_evidence or bounded_values else absent_reason,
    )


def _inventory_paths(repo: Path) -> tuple[str, ...]:
    result = run_git(
        ["ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=repo,
        check=True,
        context="inventory repository paths for audit routing",
    )
    paths = tuple(sorted(set(item for item in result.stdout.split("\0") if item)))
    if len(paths) > MAX_PATHS:
        raise CommandError(
            f"repository path inventory exceeds the {MAX_PATHS} path routing limit"
        )
    for path in paths:
        pure = PurePosixPath(path)
        if (
            pure.is_absolute()
            or ".." in pure.parts
            or "\\" in path
            or "\ufffd" in path
            or any(ord(character) < 32 for character in path)
        ):
            raise CommandError("repository path inventory contains an unsafe path")
    return paths


def _read_manifest(repo: Path, relative: str) -> str:
    path = repo / relative
    try:
        node = path.lstat()
    except OSError as exc:
        raise FingerprintEvidenceError(
            "conflicting",
            f"cannot inspect routing manifest {relative}: {_os_error_detail(exc)}",
        ) from exc
    if stat.S_ISLNK(node.st_mode) or not stat.S_ISREG(node.st_mode):
        raise FingerprintEvidenceError(
            "conflicting", f"routing manifest is not a regular file: {relative}"
        )
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(repo)
    except (OSError, ValueError) as exc:
        raise FingerprintEvidenceError(
            "conflicting", f"routing manifest escapes the repository: {relative}"
        ) from exc
    try:
        with open(
            path,
            encoding="utf-8",
            errors="strict",
            opener=_open_without_following_symlinks,
        ) as stream:
            opened = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_dev != node.st_dev
                or opened.st_ino != node.st_ino
            ):
                raise FingerprintEvidenceError(
                    "conflicting",
                    f"routing manifest changed during inspection: {relative}",
                )
            if opened.st_size > MAX_MANIFEST_BYTES:
                raise FingerprintEvidenceError(
                    "conflicting",
                    f"routing manifest exceeds {MAX_MANIFEST_BYTES} bytes: {relative}",
                )
            content = stream.read(MAX_MANIFEST_BYTES + 1)
            if len(content.encode("utf-8")) > MAX_MANIFEST_BYTES:
                raise FingerprintEvidenceError(
                    "conflicting",
                    f"routing manifest exceeds {MAX_MANIFEST_BYTES} bytes: {relative}",
                )
    except FingerprintEvidenceError:
        raise
    except (OSError, UnicodeError) as exc:
        raise FingerprintEvidenceError(
            "conflicting",
            f"cannot read routing manifest {relative}: {_os_error_detail(exc)}",
        ) from exc
    if _path_name(relative) == "package.json":
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise FingerprintEvidenceError(
                "conflicting",
                f"routing manifest is invalid JSON: {relative}: {exc}",
            ) from exc
        if not isinstance(parsed, dict):
            raise FingerprintEvidenceError(
                "conflicting",
                f"routing manifest must contain an object: {relative}",
            )
    return content.casefold()


def collect_fingerprints(repo: Path, paths: Sequence[str]) -> tuple[Fingerprint, ...]:
    source_paths = [
        path
        for path in paths
        if _is_product_path(path) and _suffix(path) in SOURCE_EXTENSIONS
    ]
    unknown_source_paths = [
        path
        for path in paths
        if _is_product_path(path)
        and any(part in SOURCE_CONTAINER_PARTS for part in _path_parts(path))
        and bool(_suffix(path))
        and _suffix(path) not in SOURCE_EXTENSIONS
        and _suffix(path) not in KNOWN_NON_SOURCE_EXTENSIONS
    ]
    if unknown_source_paths:
        evidence = ", ".join(_bounded(unknown_source_paths))
        raise FingerprintEvidenceError(
            "unknown", f"unrecognized source-language paths: {evidence}"
        )
    language_evidence: dict[str, str] = {}
    for path in source_paths:
        language_evidence.setdefault(SOURCE_EXTENSIONS[_suffix(path)], path)

    dependency_paths = [
        path for path in paths if _path_name(path) in DEPENDENCY_MANIFEST_NAMES
    ]
    manifest_text: dict[str, str] = {}
    for path in dependency_paths:
        if _path_name(path) in MANIFEST_CONTENT_NAMES:
            manifest_text[path] = _read_manifest(repo, path)

    ui_paths = [
        path
        for path in paths
        if _is_product_path(path)
        and (
            _suffix(path) in UI_EXTENSIONS
            or _has_part(
                path,
                frozenset({"frontend", "ui", "web", "pages", "components"}),
            )
        )
    ]
    ui_manifest_paths = [
        path
        for path, content in manifest_text.items()
        if any(token in content for token in UI_DEPENDENCY_TOKENS)
    ]
    datastore_paths = [
        path
        for path in paths
        if _is_product_path(path)
        and (
            _path_name(path)
            in {
                "schema.prisma",
                "schema.sql",
                "database.yml",
                "alembic.ini",
            }
            or _has_part(
                path,
                frozenset({"database", "db", "migrations", "models", "prisma"}),
            )
        )
    ]
    datastore_manifest_paths = [
        path
        for path, content in manifest_text.items()
        if any(token in content for token in DATASTORE_TOKENS)
    ]
    public_api_paths = [
        path
        for path in paths
        if _is_product_path(path)
        and (
            _path_name(path)
            in {
                "openapi.json",
                "openapi.yaml",
                "openapi.yml",
                "swagger.json",
                "swagger.yaml",
                "swagger.yml",
                "schema.graphql",
            }
            or _has_part(path, frozenset({"api", "controllers", "routes"}))
        )
    ]
    infrastructure_paths = [
        path
        for path in paths
        if _is_product_path(path)
        and (
            _suffix(path) == ".tf"
            or _path_name(path)
            in {
                "docker-compose.yml",
                "docker-compose.yaml",
                "compose.yml",
                "compose.yaml",
                "cdk.json",
                "pulumi.yaml",
                "serverless.yml",
                "serverless.yaml",
            }
            or _has_part(
                path,
                frozenset(
                    {
                        "helm",
                        "infra",
                        "infrastructure",
                        "k8s",
                        "kubernetes",
                        "terraform",
                    }
                ),
            )
        )
    ]
    deployment_paths = [
        path
        for path in paths
        if _is_product_path(path)
        and (
            _path_name(path).startswith("dockerfile")
            or _path_name(path)
            in {
                "procfile",
                "fly.toml",
                "netlify.toml",
                "render.yaml",
                "vercel.json",
            }
            or _has_part(
                path,
                frozenset({"deploy", "deployment", "helm", "k8s", "kubernetes"}),
            )
        )
    ]
    service_manifest_paths = [
        path
        for path, content in manifest_text.items()
        if any(token in content for token in SERVICE_TOKENS)
    ]
    docs_paths = [
        path
        for path in paths
        if (
            _path_name(path).startswith(("readme", "contributing", "architecture"))
            and _suffix(path) in DOCUMENTATION_EXTENSIONS
        )
        or _has_part(path, frozenset({"doc", "docs", "documentation"}))
    ]
    consumer_paths = [
        path
        for path in paths
        if _path_name(path)
        in {
            "manifest.json",
            "setup.py",
            "setup.cfg",
            "pyproject.toml",
            "package.json",
            "cargo.toml",
            "go.mod",
        }
        or _has_part(path, frozenset({"exports", "public", "templates"}))
    ]
    test_paths = [
        path
        for path in paths
        if _path_parts(path)[0] in {"test", "tests", "spec", "specs"}
        or "__tests__" in _path_parts(path)
        or _path_name(path).startswith("test_")
        or _path_name(path).endswith(("_test.py", ".test.js", ".test.ts", ".spec.js", ".spec.ts"))
    ]
    build_release_paths = [
        path
        for path in paths
        if _path_name(path)
        in {
            "makefile",
            "justfile",
            "taskfile.yml",
            "taskfile.yaml",
            "changelog.md",
            "changes.md",
        }
        or _has_part(path, frozenset({"workflows"}))
        or path in dependency_paths
    ]
    top_level_components = {
        _path_parts(path)[0]
        for path in source_paths
        if len(_path_parts(path)) > 1
    }
    batch_paths = [
        path
        for path in source_paths
        if _has_part(
            path,
            frozenset({"bin", "cli", "cmd", "commands", "jobs", "scripts", "workers"}),
        )
        or _path_name(path) in {"main.py", "main.go", "main.rs", "cli.py"}
    ]
    improvement_paths = [
        path
        for path in paths
        if _path_name(path).startswith(("roadmap", "backlog", "todo"))
        or _has_part(path, frozenset({"roadmap", "proposals", "rfcs"}))
    ]

    deployed_evidence = _bounded(
        [*deployment_paths, *service_manifest_paths]
        if deployment_paths or service_manifest_paths
        else []
    )
    fingerprints = (
        _fingerprint(
            "languages",
            language_evidence.values(),
            values=language_evidence,
            absent_reason="no recognized source-language files",
        ),
        _fingerprint(
            "runtime-code",
            source_paths,
            absent_reason="no recognized runtime source files",
        ),
        _fingerprint(
            "test-surface",
            test_paths,
            absent_reason="no conventional test paths",
        ),
        _fingerprint(
            "build-release",
            build_release_paths,
            absent_reason="no conventional build, CI, dependency, or changelog path",
        ),
        _fingerprint(
            "dependency-manifest",
            dependency_paths,
            absent_reason="no recognized dependency manifest",
        ),
        _fingerprint(
            "infrastructure",
            infrastructure_paths,
            absent_reason="no infrastructure-as-code or orchestration path",
        ),
        _fingerprint(
            "data-store",
            [*datastore_paths, *datastore_manifest_paths],
            absent_reason="no database schema, migration, or datastore dependency signal",
        ),
        _fingerprint(
            "public-api",
            public_api_paths,
            absent_reason="no API schema, route, or controller path",
        ),
        _fingerprint(
            "user-interface",
            [*ui_paths, *ui_manifest_paths],
            absent_reason="no user-interface file, path, or dependency signal",
        ),
        _fingerprint(
            "deployment",
            deployment_paths,
            absent_reason="no deployment configuration path",
        ),
        _fingerprint(
            "deployed-service",
            deployed_evidence,
            absent_reason="no deployment or service-framework signal",
        ),
        _fingerprint(
            "downstream-consumer",
            [*consumer_paths, *public_api_paths],
            absent_reason="no package, install, public API, or distributed-payload signal",
        ),
        _fingerprint(
            "documentation",
            docs_paths,
            absent_reason="no conventional repository documentation path",
        ),
        _fingerprint(
            "multi-component",
            top_level_components if len(top_level_components) > 1 else (),
            values=top_level_components if len(top_level_components) > 1 else (),
            absent_reason="fewer than two top-level runtime components",
        ),
        _fingerprint(
            "large-codebase",
            (f"source-files={len(source_paths)}",) if len(source_paths) >= 75 else (),
            absent_reason=f"recognized source-file count {len(source_paths)} is below 75",
        ),
        _fingerprint(
            "batch-runtime",
            batch_paths,
            absent_reason="no conventional CLI, batch, job, or worker entry point",
        ),
        _fingerprint(
            "improvement-signals",
            improvement_paths,
            absent_reason="no roadmap, proposal, RFC, backlog, or TODO path",
        ),
    )
    return fingerprints


def _fallback_fingerprints(reason: str, state: str) -> tuple[Fingerprint, ...]:
    return (
        Fingerprint(
            id="repository-inventory",
            state=state,
            evidence=(),
            reason=reason,
        ),
    )


def _charter_rows(
    mode: str,
    dimensions: frozenset[str],
    fingerprints: Sequence[Fingerprint],
    *,
    fallback: bool,
) -> tuple[CharterRow, ...]:
    by_id = {item.id: item for item in fingerprints}
    rows: list[CharterRow] = []
    for charter in CHARTERS:
        classification = "mandatory" if charter in MANDATORY_STANDARD else "optional"
        if fallback:
            status = "run"
            reason_code = "classification-fallback-exhaustive"
            fallback_state = fingerprints[0].state
            evidence = [f"fingerprint:repository-inventory={fallback_state}"]
        elif mode == "exhaustive":
            status = "run"
            reason_code = "exhaustive-mode"
            evidence = ["mode=exhaustive"]
        elif charter in MANDATORY_STANDARD:
            status = "run"
            reason_code = "mandatory-standard-core"
            evidence = ["mode=standard"]
        elif charter in dimensions:
            status = "run"
            reason_code = "explicit-dimension"
            evidence = [f"dimension={charter}"]
        else:
            routed_by = OPTIONAL_ROUTING[charter]
            matched = [by_id[item] for item in routed_by if by_id[item].state == "present"]
            if matched:
                status = "run"
                reason_code = "fingerprint-selected"
                evidence = []
                for item in matched:
                    evidence.append(f"fingerprint:{item.id}=present")
                    evidence.extend(item.evidence)
            else:
                status = (
                    "not-applicable"
                    if charter in CONDITIONAL_APPLICABILITY
                    else "not-selected"
                )
                reason_code = (
                    "no-applicable-fingerprint"
                    if status == "not-applicable"
                    else "no-selection-fingerprint"
                )
                evidence = [
                    f"fingerprint:{item}=absent"
                    for item in routed_by
                    if item in by_id
                ]
        rows.append(
            {
                "id": charter,
                "charterVersion": CHARTER_VERSION,
                "classification": classification,
                "status": status,
                "reasonCode": reason_code,
                "evidence": list(_bounded(evidence)),
            }
        )
    return tuple(rows)


def build_report(repo: Path, mode: str, dimensions: Sequence[str]) -> AuditReport:
    if not isinstance(repo, Path):
        raise AuditRoutingError("repository must be a path")
    if not isinstance(mode, str):
        raise AuditRoutingError("audit depth must be a string")
    if isinstance(dimensions, (str, bytes)) or not isinstance(dimensions, Sequence):
        raise AuditRoutingError("audit dimensions must be a sequence of strings")
    if any(not isinstance(item, str) for item in dimensions):
        raise AuditRoutingError("audit dimensions must contain only strings")
    if mode not in {"standard", "exhaustive"}:
        raise AuditRoutingError(f"unknown audit depth: {mode}")
    unknown = sorted(set(dimensions) - set(CHARTERS))
    if unknown:
        raise AuditRoutingError(f"unknown audit dimensions: {', '.join(unknown)}")
    if not repo.is_dir():
        raise AuditRoutingError(f"repository is not a directory: {repo}")
    repo = repo.resolve()
    requested = frozenset(dimensions)
    warnings: list[str] = []
    fallback = False
    try:
        paths = _inventory_paths(repo)
        fingerprints = collect_fingerprints(repo, paths)
    except CommandError as exc:
        fallback = True
        state = exc.state if isinstance(exc, FingerprintEvidenceError) else "unknown"
        warning = f"applicability classification failed; using exhaustive coverage: {exc}"
        warnings.append(warning)
        fingerprints = _fallback_fingerprints(str(exc), state)
    rows = _charter_rows(
        mode,
        requested,
        fingerprints,
        fallback=fallback,
    )
    counts = {
        status: sum(row["status"] == status for row in rows)
        for status in ("run", "not-applicable", "not-selected", "failed")
    }
    if mode == "standard" and not fallback:
        warnings.append(
            "standard is evidence-routed, not equivalent to exhaustive; use "
            "exhaustive for release, security, or policy-required assurance"
        )
    return {
        "schemaVersion": SCHEMA_VERSION,
        "routerVersion": ROUTER_VERSION,
        "repository": repo.name,
        "mode": mode,
        "requestedDimensions": [item for item in CHARTERS if item in requested],
        "classificationStatus": "fallback-exhaustive" if fallback else "complete",
        "fallbackToExhaustive": fallback,
        "fingerprints": [item.as_json() for item in fingerprints],
        "charters": list(rows),
        "summary": counts,
        "warnings": warnings,
    }


def render_human(report: AuditReport) -> str:
    lines = [
        f"Audit charter routing v{report['routerVersion']}",
        f"Repository: {report['repository']}",
        f"Mode: {report['mode']}",
        f"Classification: {report['classificationStatus']}",
        "Fingerprints:",
    ]
    for item in report["fingerprints"]:
        evidence = sorted(set([*item["evidence"], *item["values"]]))
        detail = ", ".join(evidence) if evidence else item["reason"] or "none"
        lines.append(f"- {item['id']}: {item['state']} — {detail}")
    lines.append("Charters:")
    for row in report["charters"]:
        row_evidence = ", ".join(row["evidence"]) or "none"
        lines.append(
            f"- {row['id']}: {row['status']} ({row['reasonCode']}) — {row_evidence}"
        )
    summary = report["summary"]
    lines.append(
        "Coverage: "
        + " · ".join(
            f"{status}={summary[status]}"
            for status in ("run", "not-applicable", "not-selected", "failed")
        )
    )
    lines.append("Warnings:")
    warnings = report["warnings"]
    if warnings:
        lines.extend(f"- {warning}" for warning in warnings)
    else:
        lines.append("- none")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument(
        "--mode",
        choices=("standard", "exhaustive"),
        default="standard",
    )
    parser.add_argument("--dimension", action="append", default=[])
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_report(args.repo, args.mode, args.dimension)
    except AuditRoutingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_human(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
