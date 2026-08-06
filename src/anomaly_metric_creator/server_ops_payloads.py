"""Declarative request-payload handling for server-mode commands.

Epic step 6a of the ``server_ops.py`` decomposition (task
``08-05-server-ops-explain-payload-extract``). Two clusters moved here
verbatim: the RFC 6902 JSON Patch operations — whose ``path`` values are
RFC 6901 JSON Pointers — used by ``kubectl patch --type=json``, and the
manifest document reader used by ``kubectl apply -f`` / ``diff`` / ``create``.

Both are pure with respect to simulation state — nothing here touches
``SimulationState`` or ``resource_snapshot``. This module never imports
``server_ops``; ``server_ops`` re-imports every name below at each block's
original position, so ``server_ops.<name>`` keeps resolving.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .server_command_render import CommandResult


def _apply_json_patch(target: dict[str, Any], operations: list[Any]) -> str:
    for operation in operations:
        if not isinstance(operation, dict):
            return "JSON patch operations must be objects"
        op = str(operation.get("op", ""))
        path = str(operation.get("path", ""))
        if op not in {"add", "replace", "remove"}:
            return f"JSON patch operation {op!r} is not modeled"
        if not path.startswith("/"):
            return f"JSON patch path {path!r} must start with /"
        if op == "remove":
            error = _remove_json_pointer(target, path)
        else:
            error = _set_json_pointer(target, path, operation.get("value"))
        if error:
            return error
    return ""


def _json_pointer_parts(path: str) -> list[str]:
    return [
        part.replace("~1", "/").replace("~0", "~")
        for part in path.lstrip("/").split("/")
        if part
    ]


def _set_json_pointer(target: dict[str, Any], path: str, value: Any) -> str:
    parts = _json_pointer_parts(path)
    if not parts:
        return "JSON patch root replacement is not modeled"
    cursor: dict[str, Any] = target
    for part in parts[:-1]:
        child = cursor.setdefault(part, {})
        if not isinstance(child, dict):
            return f"JSON patch path {path!r} crosses a non-object value"
        cursor = child
    cursor[parts[-1]] = value
    return ""


def _remove_json_pointer(target: dict[str, Any], path: str) -> str:
    parts = _json_pointer_parts(path)
    if not parts:
        return "JSON patch root removal is not modeled"
    cursor: dict[str, Any] = target
    for part in parts[:-1]:
        child = cursor.get(part)
        if not isinstance(child, dict):
            return f"JSON patch path {path!r} does not exist"
        cursor = child
    if parts[-1] not in cursor:
        return f"JSON patch path {path!r} does not exist"
    del cursor[parts[-1]]
    return ""


def _load_manifest_documents(path: Path) -> list[dict[str, Any]] | CommandResult:
    suffix = path.suffix.lower()
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return CommandResult(
            1,
            "",
            f"error: unable to read manifest {path}: {exc}\n",
            "partial",
            "kubectl.apply.manifest.read",
        )
    try:
        if suffix == ".json":
            raw = json.loads(text)
            documents = raw if isinstance(raw, list) else [raw]
        elif suffix in {".yaml", ".yml"}:
            try:
                import yaml  # type: ignore[import-not-found]
            except ModuleNotFoundError:
                return CommandResult(
                    1,
                    "",
                    f"error: PyYAML is required to parse manifest {path}\n",
                    "partial",
                    "kubectl.apply.manifest.yaml",
                )
            try:
                documents = list(yaml.safe_load_all(text))
            except yaml.YAMLError as exc:
                return CommandResult(
                    1,
                    "",
                    f"error: invalid manifest {path}: {exc}\n",
                    "partial",
                    "kubectl.apply.manifest.parse",
                )
        else:
            return CommandResult(
                1,
                "",
                f"error: unsupported manifest extension for {path}; use .json, .yaml, or .yml\n",
                "partial",
                "kubectl.apply.manifest.extension",
            )
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return CommandResult(
            1,
            "",
            f"error: invalid manifest {path}: {exc}\n",
            "partial",
            "kubectl.apply.manifest.parse",
        )
    return _normalize_manifest_documents(documents, str(path))


def _normalize_manifest_documents(documents: list[Any], source: str) -> list[dict[str, Any]] | CommandResult:
    normalized: list[dict[str, Any]] = []
    for index, document in enumerate(documents, start=1):
        if document is None:
            continue
        if not isinstance(document, dict):
            return CommandResult(
                1,
                "",
                f"error: manifest {source} document {index} must be a Kubernetes object\n",
                "partial",
                "kubectl.apply.manifest.shape",
            )
        if str(document.get("kind", "")).lower() == "list":
            items = document.get("items")
            if not isinstance(items, list):
                return CommandResult(
                    1,
                    "",
                    f"error: manifest {source} document {index} List.items must be a list\n",
                    "partial",
                    "kubectl.apply.manifest.shape",
                )
            for item_index, item in enumerate(items, start=1):
                if not isinstance(item, dict):
                    return CommandResult(
                        1,
                        "",
                        f"error: manifest {source} document {index} item {item_index} must be a Kubernetes object\n",
                        "partial",
                        "kubectl.apply.manifest.shape",
                    )
                normalized.append(item)
            continue
        normalized.append(document)
    return normalized
