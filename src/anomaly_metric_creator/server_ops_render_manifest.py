"""Manifest and patch kubectl renderers for the serve-mode simulator.

This leaf owns ``kubectl patch`` (strategic/merge and RFC 6902 JSON patch
payloads), ``diff``, ``apply -f`` and ``create``, plus the manifest-target
resolution and the generic resource-row builders they share. Each renderer
reads the overlay-aware ``resource_snapshot()`` from ``server_ops_snapshot``
and the row filter from ``server_ops_render``; a test that stubs the snapshot
for these renderers patches it here. ``server_ops`` re-imports the names its callers
read through it, so the ``server_ops.<name>`` and ``server.<name>``
surfaces they use stay stable. This module never imports
``server_ops`` at runtime; ``SimulationState`` is a type-only import.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .server_command_render import CommandResult, _is_dry_run, _not_found
from .server_k8s_api import _payload_replicas
from .server_k8s_objects import _k8s_workload_labels
from .server_mutations import _resource_prefix
from .server_ops_parse import _first_flag_value, _flag_values, _normalize_kind
from .server_ops_payloads import _apply_json_patch, _load_manifest_documents
from .server_ops_render import _filter_snapshot_rows
from .server_ops_snapshot import _node_rows, _stable_cluster_ip, resource_snapshot
from .server_ops_support import _dict_or_empty, _find_named, _string_dict

if TYPE_CHECKING:
    from .server_ops import SimulationState
    from .server_ops_parse import ParsedCommand


_MUTATION_SNAPSHOT_KINDS = {
    "configmaps",
    "secrets",
    "deployments",
    "daemonsets",
    "services",
    "hpa",
    "jobs",
    "cronjobs",
    "serviceaccounts",
    "pvc",
    "statefulsets",
    "ingress",
}


def _render_patch(state: SimulationState, parsed: ParsedCommand) -> CommandResult:
    kind = parsed.resource_kind
    name = parsed.resource_name
    snapshot_kind = _mutation_snapshot_kind(kind)
    if not snapshot_kind or not name:
        return CommandResult(
            1,
            "",
            f"error: unsupported patch target {kind or '<missing-kind>'}/{name or '<missing-name>'}\n",
            "unsupported",
            "kubectl.patch.unsupported",
        )
    parsed_payload = _patch_payload(state, parsed)
    if isinstance(parsed_payload, CommandResult):
        return parsed_payload
    payload = parsed_payload
    now = state.clock.now()
    replicas = _payload_replicas(payload)
    if snapshot_kind == "deployments" and name:
        # Patching a deployment that is not in the overlay-aware snapshot must
        # 404 before any write, matching the API deployment-patch path — the
        # command path used to set_workload on a ghost name (audit A-013). The
        # generic (non-deployment) branch below keeps its upsert semantics,
        # which the API generic PATCH/PUT path also uses, so the two stay in
        # parity.
        if _find_named(resource_snapshot(state)["deployments"], name) is None:
            return _not_found("deployments", name)
    if snapshot_kind == "deployments" and replicas is not None:
        state.mutations.set_workload(
            name,
            now=now,
            replicas=replicas,
            ready_replicas=replicas,
            deployment_status="Healthy" if replicas else "ScaledToZero",
            pod_status="Running",
        )
        state.mutations.record_event(
            "Normal",
            "Patched",
            f"deployment/{name}",
            f"patched deployment {name} replicas to {replicas}",
            now,
        )
    else:
        state.mutations.put_resource(
            snapshot_kind,
            name,
            _generic_resource_row(state, snapshot_kind, name, payload=payload, parsed=parsed),
            now=now,
            namespace=parsed.namespace,
        )
    return CommandResult(
        0,
        f"{_resource_prefix(snapshot_kind)}/{name} patched\n",
        "",
        "supported",
        f"kubectl.patch.{snapshot_kind}",
    )


def _patch_payload(state: SimulationState, parsed: ParsedCommand) -> dict[str, Any] | CommandResult:
    payload_text = _patch_payload_text(parsed)
    if not payload_text:
        return CommandResult(
            1,
            "",
            "error: kubectl patch requires --patch/-p JSON payload\n",
            "partial",
            "kubectl.patch.payload",
        )
    try:
        raw_payload = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        return CommandResult(
            1,
            "",
            f"error: invalid patch JSON: {exc.msg}\n",
            "partial",
            "kubectl.patch.payload.invalid",
        )
    patch_type = str(parsed.flags.get("--type") or "strategic").strip().lower()
    if patch_type in {"merge", "strategic", "strategic-merge"}:
        if not isinstance(raw_payload, dict):
            return CommandResult(
                1,
                "",
                "error: merge and strategic patches must be JSON objects\n",
                "partial",
                "kubectl.patch.payload.shape",
            )
        base = _patch_base_payload(state, parsed)
        return _deep_merge_patch(base, raw_payload)
    if patch_type == "json":
        if not isinstance(raw_payload, list):
            return CommandResult(
                1,
                "",
                "error: JSON patch payload must be a list of operations\n",
                "partial",
                "kubectl.patch.payload.shape",
            )
        base = _patch_base_payload(state, parsed)
        error = _apply_json_patch(base, raw_payload)
        if error:
            return CommandResult(1, "", f"error: {error}\n", "partial", "kubectl.patch.json")
        return base
    return CommandResult(
        1,
        "",
        f"error: patch type {patch_type!r} is not modeled; use merge, strategic, or json\n",
        "partial",
        "kubectl.patch.type",
    )


def _patch_payload_text(parsed: ParsedCommand) -> str:
    payload = _first_flag_value(parsed.flags, "--patch")
    if payload:
        return payload
    p_value = parsed.flags.get("-p")
    if isinstance(p_value, str) and p_value:
        return p_value
    for token in reversed(parsed.positionals[2:]):
        stripped = token.strip()
        if stripped.startswith(("{", "[")):
            return stripped
    return ""


def _patch_base_payload(state: SimulationState, parsed: ParsedCommand) -> dict[str, Any]:
    snapshot_kind = _mutation_snapshot_kind(parsed.resource_kind)
    row = None
    if snapshot_kind:
        rows = _filter_snapshot_rows(snapshot_kind, resource_snapshot(state).get(snapshot_kind, []), parsed)
        row = _find_named(rows, parsed.resource_name)
    payload: dict[str, Any] = {
        "metadata": {
            "name": parsed.resource_name,
            "namespace": parsed.namespace,
        },
    }
    if row is None:
        return payload
    labels = _string_dict(row.get("labels"))
    annotations = _string_dict(row.get("annotations"))
    if labels:
        payload["metadata"]["labels"] = labels
    if annotations:
        payload["metadata"]["annotations"] = annotations
    if snapshot_kind == "configmaps":
        payload["data"] = {str(key): str(value) for key, value in row.get("keys", {}).items()}
    elif snapshot_kind == "services":
        payload["spec"] = {
            "type": row.get("type", "ClusterIP"),
            "clusterIP": row.get("cluster_ip"),
            "selector": _dict_or_empty(row.get("selector")),
            "ports": [{"port": row.get("port", 8080)}],
        }
    elif snapshot_kind in {"deployments", "statefulsets"}:
        ready = str(row.get("ready", "1/1"))
        _, _, desired = ready.partition("/")
        payload["spec"] = {"replicas": desired or "1"}
    return payload


def _deep_merge_patch(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    for key, value in patch.items():
        if value is None:
            base.pop(str(key), None)
        elif isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge_patch(base[key], value)
        else:
            base[str(key)] = value
    return base


def _render_diff(state: SimulationState, parsed: ParsedCommand) -> CommandResult:
    filename = _first_flag_value(parsed.flags, "-f", "--filename")
    if not filename:
        return CommandResult(
            1,
            "",
            "error: kubectl diff requires -f/--filename for simulator-backed manifests\n",
            "partial",
            "kubectl.diff.filename",
        )
    kind, name = _resource_from_manifest_name(filename)
    snapshot_kind = _mutation_snapshot_kind(kind)
    prefix = _resource_prefix(snapshot_kind or kind)
    existing = _find_named(resource_snapshot(state).get(snapshot_kind, []), name) if snapshot_kind else None
    status = "existing" if existing else "new"
    stdout = (
        f"diff -u -N current/{prefix}/{name} desired/{prefix}/{name}\n"
        f"--- current/{prefix}/{name}\n"
        f"+++ desired/{prefix}/{name}\n"
        "@@\n"
        f"- simulator-state: {status}\n"
        f"+ simulator-manifest: {filename}\n"
    )
    return CommandResult(1, stdout, "", "supported", "kubectl.diff")


def _render_apply(state: SimulationState, parsed: ParsedCommand) -> CommandResult:
    if parsed.verb == "create":
        return _render_create(state, parsed)
    filenames = _flag_values(parsed.flags, "-f", "--filename") or ["manifest"]
    now = state.clock.now()
    dry_run = _is_dry_run(parsed)
    action = "configured"
    targets: list[tuple[str, str, str, dict[str, Any], str]] = []
    for filename in filenames:
        manifest_targets = _manifest_apply_targets(state, parsed, str(filename))
        if isinstance(manifest_targets, CommandResult):
            return manifest_targets
        targets.extend(manifest_targets)
    if not targets:
        return CommandResult(
            1,
            "",
            "error: kubectl apply did not find supported simulator resources\n",
            "partial",
            "kubectl.apply.manifest.empty",
        )
    if not dry_run:
        for snapshot_kind, name, namespace, payload, _source in targets:
            state.mutations.put_resource(
                snapshot_kind,
                name,
                _generic_resource_row(state, snapshot_kind, name, payload=payload, parsed=parsed),
                now=now,
                namespace=namespace,
            )
    suffix = " (dry run)" if dry_run else ""
    stdout = "".join(
        f"{_resource_prefix(snapshot_kind)}/{name} {action}{suffix}\n"
        for snapshot_kind, name, _namespace, _payload, _source in targets
    )
    return CommandResult(0, stdout, "", "supported", "kubectl.apply.manifest")


def _render_create(state: SimulationState, parsed: ParsedCommand) -> CommandResult:
    now = state.clock.now()
    kind = parsed.resource_kind
    name = parsed.resource_name
    snapshot_kind = _mutation_snapshot_kind(kind)
    dry_run = _is_dry_run(parsed)
    if snapshot_kind and name and not dry_run:
        state.mutations.put_resource(
            snapshot_kind,
            name,
            _generic_resource_row(state, snapshot_kind, name, payload={}, parsed=parsed),
            now=now,
            namespace=parsed.namespace,
        )
    elif not dry_run:
        state.mutations.record_event(
            "Normal",
            "Applied",
            "manifest/simulated",
            f"{parsed.verb} accepted manifest; simulator state reconciled",
            now,
        )
    target = f"{_resource_prefix(snapshot_kind)}/{name}" if snapshot_kind and name else "manifest"
    suffix = " (dry run)" if dry_run else ""
    return CommandResult(0, f"{target} created{suffix}\n", "", "supported", "kubectl.create")


def _manifest_apply_targets(
    state: SimulationState,
    parsed: ParsedCommand,
    filename: str,
) -> list[tuple[str, str, str, dict[str, Any], str]] | CommandResult:
    path = Path(filename)
    if filename == "-":
        return CommandResult(
            1,
            "",
            "error: kubectl apply from stdin is not modeled; use -f PATH\n",
            "partial",
            "kubectl.apply.manifest.stdin",
        )
    if not path.exists():
        kind, name = _resource_from_manifest_name(filename)
        snapshot_kind = _mutation_snapshot_kind(kind)
        if snapshot_kind and name:
            namespace = state.namespace if parsed.namespace == "*" else parsed.namespace
            return [(snapshot_kind, name, namespace, {}, filename)]
        return []
    if not path.is_file():
        return CommandResult(
            1,
            "",
            f"error: kubectl apply -f {filename}: path is not a regular file\n",
            "partial",
            "kubectl.apply.manifest.read",
        )
    documents = _load_manifest_documents(path)
    if isinstance(documents, CommandResult):
        return documents
    targets: list[tuple[str, str, str, dict[str, Any], str]] = []
    for index, payload in enumerate(documents, start=1):
        target = _manifest_apply_target(state, parsed, payload, filename, index)
        if isinstance(target, CommandResult):
            return target
        targets.append(target)
    return targets


def _manifest_apply_target(
    state: SimulationState,
    parsed: ParsedCommand,
    payload: dict[str, Any],
    source: str,
    index: int,
) -> tuple[str, str, str, dict[str, Any], str] | CommandResult:
    raw_kind = str(payload.get("kind") or "").strip()
    metadata = _dict_or_empty(payload.get("metadata"))
    name = str(metadata.get("name") or "").strip()
    if not raw_kind or not name:
        return CommandResult(
            1,
            "",
            f"error: manifest {source} document {index} requires kind and metadata.name\n",
            "partial",
            "kubectl.apply.manifest.identity",
        )
    snapshot_kind = _mutation_snapshot_kind(raw_kind)
    if not snapshot_kind:
        return CommandResult(
            1,
            "",
            f"error: manifest {source} document {index} kind {raw_kind!r} is not modeled by the simulator\n",
            "partial",
            "kubectl.apply.manifest.unsupported",
        )
    namespace = str(metadata.get("namespace") or parsed.namespace or state.namespace)
    if namespace == "*":
        namespace = state.namespace
    return snapshot_kind, name, namespace, payload, source


def _resource_from_manifest_name(filename: str) -> tuple[str, str]:
    stem = Path(filename).name
    for suffix in (".yaml", ".yml", ".json"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
    tokens = [token for token in stem.replace("_", "-").split("-") if token]
    aliases = {
        "configmap": "configmaps",
        "cm": "configmaps",
        "secret": "secrets",
        "service": "services",
        "svc": "services",
        "deployment": "deployments",
        "deploy": "deployments",
        "job": "jobs",
        "cronjob": "cronjobs",
        "ingress": "ingress",
        "hpa": "hpa",
        "serviceaccount": "serviceaccounts",
    }
    if tokens and tokens[0] in aliases and len(tokens) > 1:
        return aliases[tokens[0]], "-".join(tokens[1:])
    if tokens and tokens[-1] in aliases and len(tokens) > 1:
        return aliases[tokens[-1]], "-".join(tokens[:-1])
    return "configmaps", stem or "simulated-manifest"


def _mutation_snapshot_kind(kind: str) -> str:
    normalized = _normalize_kind(kind)
    aliases = {
        "horizontalpodautoscalers": "hpa",
        "persistentvolumeclaims": "pvc",
        "ingresses": "ingress",
        "manifest": "configmaps",
    }
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in _MUTATION_SNAPSHOT_KINDS else ""


def _generic_resource_row(
    state: SimulationState,
    kind: str,
    name: str,
    *,
    payload: dict[str, Any],
    parsed: ParsedCommand | None = None,
) -> dict[str, Any]:
    spec = _dict_or_empty(payload.get("spec"))
    data = _dict_or_empty(payload.get("data"))
    string_data = _dict_or_empty(payload.get("stringData"))
    base = _generic_resource_metadata(state, kind, name, payload=payload, parsed=parsed)

    def row(values: dict[str, Any]) -> dict[str, Any]:
        return {**base, **values}

    if kind == "configmaps":
        keys = {str(key): str(value) for key, value in data.items()} or _configmap_keys_from_flags(parsed)
        if not keys:
            keys = {"simulated": "true"}
        return row({"name": name, "data": len(keys), "age": "0s", "keys": keys})
    if kind == "secrets":
        secret_data = {str(key): str(value) for key, value in {**data, **string_data}.items()}
        return row({"name": name, "type": payload.get("type", "Opaque"), "data": len(secret_data) or 1, "age": "0s"})
    if kind == "services":
        service_type = str(spec.get("type") or "ClusterIP")
        ports = spec.get("ports") if isinstance(spec.get("ports"), list) else []
        port = ports[0].get("port", 8080) if ports and isinstance(ports[0], dict) else 8080
        selector = _dict_or_empty(spec.get("selector"))
        return row({
            "name": name,
            "type": service_type,
            "cluster_ip": str(spec.get("clusterIP") or _stable_cluster_ip(name)),
            "external_ip": "<none>",
            "ports": f"{port}/TCP",
            "port": port,
            "selector": {str(key): str(value) for key, value in selector.items()} or {"app.kubernetes.io/name": name},
            "age": "0s",
        })
    if kind == "deployments":
        replicas = _payload_replicas(payload)
        if replicas is None:
            replicas = 1
        return row({
            "name": name,
            "ready": f"{replicas}/{replicas}",
            "up_to_date": replicas,
            "available": replicas,
            "age": "0s",
            "status": "Healthy" if replicas else "ScaledToZero",
            "generation": int(base.get("generation", 1) or 1),
            "observed_generation": int(base.get("generation", 1) or 1),
        })
    if kind == "serviceaccounts":
        return row({"name": name, "secrets": len(payload.get("secrets", [])), "age": "0s"})
    if kind == "hpa":
        min_replicas = int(spec.get("minReplicas", 1) or 1)
        max_replicas = int(spec.get("maxReplicas", 8) or 8)
        target = _dict_or_empty(spec.get("scaleTargetRef"))
        target_name = str(target.get("name") or name)
        return row({
            "name": name,
            "reference": f"{target.get('kind', 'Deployment')}/{target_name}",
            "targets": "0%/80%",
            "minpods": min_replicas,
            "maxpods": max_replicas,
            "replicas": min_replicas,
            "age": "0s",
        })
    if kind == "jobs":
        completions = int(spec.get("completions", 1) or 1)
        return row({"name": name, "completions": f"0/{completions}", "duration": "0s", "age": "0s"})
    if kind == "cronjobs":
        schedule = str(spec.get("schedule") or (parsed.flags.get("--schedule") if parsed else "") or "* * * * *")
        return row({"name": name, "schedule": schedule, "suspend": "False", "active": 0, "last_schedule": "<none>", "age": "0s"})
    if kind == "pvc":
        requests = spec.get("resources", {}).get("requests", {}) if isinstance(spec.get("resources"), dict) else {}
        access_modes = spec.get("accessModes", ["RWO"])
        if not isinstance(access_modes, list):
            access_modes = ["RWO"]
        return row({
            "name": name,
            "status": "Bound",
            "volume": f"pvc-{name}",
            "capacity": str(requests.get("storage", "1Gi")),
            "access_modes": ",".join(str(mode) for mode in access_modes),
            "storageclass": str(spec.get("storageClassName", "gp3")),
            "age": "0s",
            "used_pct": 1,
        })
    if kind == "statefulsets":
        replicas = _payload_replicas(payload)
        if replicas is None:
            replicas = 1
        return row({"name": name, "ready": f"{replicas}/{replicas}", "age": "0s"})
    if kind == "daemonsets":
        nodes = _node_rows(state)
        return row({
            "name": name,
            "desired": len(nodes),
            "current": len(nodes),
            "ready": len(nodes),
            "up_to_date": len(nodes),
            "available": len(nodes),
            "node_selector": "kubernetes.io/os=linux",
            "age": "0s",
        })
    if kind == "ingress":
        rules = spec.get("rules") if isinstance(spec.get("rules"), list) else []
        host = rules[0].get("host") if rules and isinstance(rules[0], dict) else f"{name}.simulated-saas.local"
        return row({"name": name, "class": spec.get("ingressClassName", "nginx"), "hosts": host, "address": "10.0.0.20", "ports": "80,443", "age": "0s"})
    return row({"name": name, "age": "0s"})


def _generic_resource_metadata(
    state: SimulationState,
    kind: str,
    name: str,
    *,
    payload: dict[str, Any],
    parsed: ParsedCommand | None = None,
) -> dict[str, Any]:
    metadata = _dict_or_empty(payload.get("metadata"))
    spec = _dict_or_empty(payload.get("spec"))
    namespace = str(metadata.get("namespace") or (parsed.namespace if parsed else "") or state.namespace)
    if namespace == "*":
        namespace = state.namespace
    labels = _string_dict(metadata.get("labels"))
    annotations = _string_dict(metadata.get("annotations"))
    selector = _dict_or_empty(spec.get("selector"))
    match_labels = _dict_or_empty(selector.get("matchLabels"))
    template = _dict_or_empty(spec.get("template"))
    template_metadata = _dict_or_empty(template.get("metadata"))
    template_labels = _string_dict(template_metadata.get("labels"))
    if kind in {"deployments", "statefulsets", "daemonsets"}:
        labels = {**_k8s_workload_labels(name), **labels}
        if not match_labels:
            match_labels = {"app.kubernetes.io/name": name}
        if not template_labels:
            template_labels = {**labels, **{str(key): str(value) for key, value in match_labels.items()}}
    generation = metadata.get("generation", 1)
    try:
        generation = max(1, int(str(generation)))
    except (TypeError, ValueError):
        generation = 1
    result: dict[str, Any] = {
        "namespace": namespace,
        "labels": labels,
        "annotations": annotations,
        "generation": generation,
        "observed_generation": generation,
        "resource_version": "1",
    }
    if match_labels:
        result["selector"] = {str(key): str(value) for key, value in match_labels.items()}
    if template_labels:
        result["template_labels"] = template_labels
    owner_references = metadata.get("ownerReferences")
    if isinstance(owner_references, list):
        result["owner_references"] = [
            dict(item) for item in owner_references
            if isinstance(item, dict)
        ]
    deletion_timestamp = metadata.get("deletionTimestamp")
    if deletion_timestamp:
        result["deletion_timestamp"] = str(deletion_timestamp)
    return result


def _configmap_keys_from_flags(parsed: ParsedCommand | None) -> dict[str, str]:
    if parsed is None:
        return {}
    keys: dict[str, str] = {}
    for literal in _flag_values(parsed.flags, "--from-literal"):
        key, _, value = literal.partition("=")
        keys[key or "literal"] = value or "true"
    for from_file in _flag_values(parsed.flags, "--from-file"):
        key, separator, path = from_file.partition("=")
        if not separator:
            path = key
            key = Path(path).name or "file"
        keys[key or "file"] = f"file:{path or 'true'}"
    return keys
