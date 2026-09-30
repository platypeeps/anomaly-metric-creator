"""Read-only kubectl renderers for the serve-mode simulator.

This leaf owns ``kubectl get`` (including ``--watch`` and ``get all``),
``describe``, the ``logs`` family, ``top``, ``version``, ``api-versions``,
``api-resources`` and ``cluster-info``, plus their row filters and logs
option helpers. Each renderer reads the overlay-aware ``resource_snapshot()``
from ``server_ops_snapshot``; a test that stubs the snapshot for these
renderers patches it here. ``server_ops`` re-imports every name at its
original position, so the historic ``server_ops.<name>`` and
``server.<name>`` surfaces stay stable. This module never imports
``server_ops`` at runtime; ``SimulationState`` is a type-only import.
"""

from __future__ import annotations

import contextlib
import json
from typing import TYPE_CHECKING, Any

from .server_command_render import CommandResult, _not_found, _table, _unsupported
from .server_k8s_api import (
    _K8S_ADVERTISED_GIT_VERSION,
    _K8S_ADVERTISED_TAG,
    _matches_label_selector,
    _split_selector,
)
from .server_mutations import _format_dt, _resource_prefix
from .server_ops_snapshot import (
    _SNAPSHOT_KINDS,
    _component_events,
    _snapshot_kind_namespaced,
    resource_snapshot,
)
from .server_ops_support import (
    _component_from_name,
    _find_named,
    _parse_user_timestamp,
    _snapshot_row_labels,
    _snapshot_row_namespace,
)

if TYPE_CHECKING:
    import datetime as _dt

    from .server_ops import SimulationState
    from .server_ops_parse import ParsedCommand


_WATCH_COMMAND_NOTE = (
    "watch: live streaming is not available over the one-shot command API; "
    "fetch /v1/kubeconfig and use real kubectl for --watch\n"
)


def _render_get_watch(
    state: SimulationState, kind: str, parsed: ParsedCommand
) -> CommandResult:
    """Render `kubectl get --watch` as a one-shot table plus a partial note.

    `POST /v1/commands` cannot hold a stream open, so a `--watch` request
    renders the initial table exactly as the plain `get` would, appends one
    stderr note pointing at real kubectl, exits 0, and classifies the trace
    `partial` (rule `kubectl.get.<kind>.watch`) so the ignored flag surfaces
    in the debug backlog instead of being silently swallowed.
    """
    if kind not in _SNAPSHOT_KINDS and kind != "all":
        return _unsupported(parsed, f"kubectl get {kind or '<missing-kind>'} --watch")
    return CommandResult(
        0,
        _render_get(state, kind, parsed),
        _WATCH_COMMAND_NOTE,
        "partial",
        f"kubectl.get.{kind}.watch",
    )


def _render_get(state: SimulationState, kind: str, parsed: ParsedCommand) -> str:
    resources = resource_snapshot(state)
    if kind == "all":
        return _render_get_all(state, parsed)
    rows = _filter_snapshot_rows(kind, resources.get(kind, []), parsed)
    if "-o" in parsed.flags or "--output" in parsed.flags:
        output = parsed.flags.get("-o", parsed.flags.get("--output"))
        if output == "json":
            return json.dumps({"items": rows}, indent=2) + "\n"
        if output == "name":
            return "".join(f"{_resource_prefix(kind)}/{row['name']}\n" for row in rows)
    if kind == "pods":
        if parsed.flags.get("-o") == "wide" or parsed.flags.get("--output") == "wide":
            return _table(["NAME", "READY", "STATUS", "RESTARTS", "AGE", "IP", "NODE"], [
                [r["name"], r["ready"], r["status"], str(r["restarts"]), r["age"], r["pod_ip"], r["node"]]
                for r in rows
            ])
        return _table(["NAME", "READY", "STATUS", "RESTARTS", "AGE"], [
            [r["name"], r["ready"], r["status"], str(r["restarts"]), r["age"]]
            for r in rows
        ])
    if kind == "namespaces":
        return _table(["NAME", "STATUS", "AGE"], [
            [r["name"], r["status"], r["age"]]
            for r in rows
        ])
    if kind == "configmaps":
        return _table(["NAME", "DATA", "AGE"], [
            [r["name"], str(r["data"]), r["age"]]
            for r in rows
        ])
    if kind == "secrets":
        return _table(["NAME", "TYPE", "DATA", "AGE"], [
            [r["name"], r["type"], str(r["data"]), r["age"]]
            for r in rows
        ])
    if kind == "replicationcontrollers":
        return _table(["NAME", "DESIRED", "CURRENT", "READY", "AGE"], [])
    if kind == "deployments":
        return _table(["NAME", "READY", "UP-TO-DATE", "AVAILABLE", "AGE"], [
            [r["name"], r["ready"], str(r["up_to_date"]), str(r["available"]), r["age"]]
            for r in rows
        ])
    if kind == "replicasets":
        return _table(["NAME", "DESIRED", "CURRENT", "READY", "AGE"], [
            [r["name"], str(r["desired"]), str(r["current"]), str(r["ready"]), r["age"]]
            for r in rows
        ])
    if kind == "daemonsets":
        return _table(["NAME", "DESIRED", "CURRENT", "READY", "UP-TO-DATE", "AVAILABLE", "NODE SELECTOR", "AGE"], [
            [
                r["name"], str(r["desired"]), str(r["current"]), str(r["ready"]),
                str(r["up_to_date"]), str(r["available"]), r["node_selector"], r["age"],
            ]
            for r in rows
        ])
    if kind == "services":
        return _table(["NAME", "TYPE", "CLUSTER-IP", "EXTERNAL-IP", "PORT(S)", "AGE"], [
            [r["name"], r["type"], r["cluster_ip"], r["external_ip"], r["ports"], r["age"]]
            for r in rows
        ])
    if kind == "endpoints":
        return _table(["NAME", "ENDPOINTS", "AGE"], [
            [r["name"], r["endpoints"] or "<none>", r["age"]]
            for r in rows
        ])
    if kind == "endpointslices":
        return _table(["NAME", "ADDRESSTYPE", "PORTS", "ENDPOINTS", "AGE"], [
            [r["name"], r["address_type"], r["ports"], str(r["endpoints"]), r["age"]]
            for r in rows
        ])
    if kind == "events":
        return _table(["LAST SEEN", "TYPE", "REASON", "OBJECT", "MESSAGE"], [
            [r["last_seen"], r["type"], r["reason"], r["object"], r["message"]]
            for r in rows
        ])
    if kind == "hpa":
        return _table(["NAME", "REFERENCE", "TARGETS", "MINPODS", "MAXPODS", "REPLICAS", "AGE"], [
            [r["name"], r["reference"], r["targets"], str(r["minpods"]), str(r["maxpods"]), str(r["replicas"]), r["age"]]
            for r in rows
        ])
    if kind == "jobs":
        return _table(["NAME", "COMPLETIONS", "DURATION", "AGE"], [
            [r["name"], r["completions"], r["duration"], r["age"]]
            for r in rows
        ])
    if kind == "cronjobs":
        return _table(["NAME", "SCHEDULE", "SUSPEND", "ACTIVE", "LAST SCHEDULE", "AGE"], [
            [r["name"], r["schedule"], r["suspend"], str(r["active"]), r["last_schedule"], r["age"]]
            for r in rows
        ])
    if kind == "serviceaccounts":
        return _table(["NAME", "SECRETS", "AGE"], [
            [r["name"], str(r["secrets"]), r["age"]]
            for r in rows
        ])
    if kind == "nodes":
        return _table(["NAME", "STATUS", "ROLES", "AGE", "VERSION"], [
            [r["name"], r["status"], r["roles"], r["age"], r["version"]]
            for r in rows
        ])
    if kind == "pvc":
        return _table(["NAME", "STATUS", "VOLUME", "CAPACITY", "ACCESS MODES", "STORAGECLASS", "AGE"], [
            [r["name"], r["status"], r["volume"], r["capacity"], r["access_modes"], r["storageclass"], r["age"]]
            for r in rows
        ])
    if kind == "statefulsets":
        return _table(["NAME", "READY", "AGE"], [
            [r["name"], r["ready"], r["age"]]
            for r in rows
        ])
    if kind == "ingress":
        return _table(["NAME", "CLASS", "HOSTS", "ADDRESS", "PORTS", "AGE"], [
            [r["name"], r["class"], r["hosts"], r["address"], r["ports"], r["age"]]
            for r in rows
        ])
    return ""


def _render_get_all(state: SimulationState, parsed: ParsedCommand) -> str:
    resources = resource_snapshot(state)
    rows = []
    for kind in ("pods", "services", "deployments", "replicasets", "statefulsets", "hpa", "jobs", "cronjobs"):
        for row in _filter_snapshot_rows(kind, resources.get(kind, []), parsed):
            status = (
                row.get("status")
                or row.get("ready")
                or row.get("targets")
                or row.get("completions")
                or row.get("schedule")
                or "Active"
            )
            rows.append([_resource_prefix(kind), row["name"], str(status), row.get("age", "7d")])
    if parsed.flags.get("-o") == "name" or parsed.flags.get("--output") == "name":
        return "".join(f"{kind}/{name}\n" for kind, name, _, _ in rows)
    return _table(["KIND", "NAME", "STATUS", "AGE"], rows)


def _filter_snapshot_rows(
    kind: str,
    rows: list[dict[str, Any]],
    parsed: ParsedCommand,
) -> list[dict[str, Any]]:
    label_selector = str(parsed.flags.get("-l") or parsed.flags.get("--selector") or "")
    field_selector = str(parsed.flags.get("--field-selector") or "")
    return [
        row for row in rows
        if _snapshot_row_matches_namespace(kind, row, parsed.namespace)
        and _matches_label_selector(_snapshot_row_labels(kind, row), label_selector)
        and _snapshot_row_matches_field_selector(kind, row, field_selector)
    ]


def _snapshot_row_matches_namespace(kind: str, row: dict[str, Any], namespace: str) -> bool:
    if namespace == "*" or not _snapshot_kind_namespaced(kind):
        return True
    return _snapshot_row_namespace(row) == namespace


def _snapshot_row_matches_field_selector(kind: str, row: dict[str, Any], selector: str) -> bool:
    if not selector:
        return True
    fields = {
        "metadata.name": row.get("name", ""),
        "status.phase": "Running" if row.get("status") == "Running" else row.get("status", ""),
        "involvedObject.name": str(row.get("object", "")).split("/", 1)[-1],
        "kind": kind,
    }
    for item in _split_selector(selector):
        if "!=" in item:
            key, value = item.split("!=", 1)
            if str(fields.get(key.strip(), "")) == value.strip():
                return False
        elif "==" in item or "=" in item:
            separator = "==" if "==" in item else "="
            key, value = item.split(separator, 1)
            if str(fields.get(key.strip(), "")) != value.strip():
                return False
    return True


def _render_describe(state: SimulationState, kind: str, parsed: ParsedCommand) -> CommandResult:
    name = parsed.resource_name
    resources = resource_snapshot(state)
    if kind == "pods":
        pod = _find_named(resources["pods"], name)
        if pod is None:
            return _not_found("pods", name)
        lines = [
            f"Name:           {pod['name']}",
            f"Namespace:      {state.namespace}",
            f"Node:           {pod['node']}",
            f"Status:         {pod['status']}",
            f"Controlled By:  ReplicaSet/{pod['component']}",
            "Containers:",
            f"  {pod['component']}:",
            f"    Ready:      {pod['ready'].split('/')[0] == pod['ready'].split('/')[1]}",
            f"    Restarts:   {pod['restarts']}",
            "Events:",
        ]
        lines.extend("  " + event for event in pod["events"])
        return CommandResult(0, "\n".join(lines) + "\n", "", "supported", "kubectl.describe.pods")
    if kind == "deployments":
        deployment = _find_named(resources["deployments"], name)
        if deployment is None:
            return _not_found("deployments", name)
        component = deployment["name"]
        events = _component_events(state, component)
        lines = [
            f"Name:                   {component}",
            f"Namespace:              {state.namespace}",
            f"Replicas:               {deployment['ready']} available",
            f"DeploymentStatus:       {deployment['status']}",
            "Conditions:",
            "  Type           Status  Reason",
            f"  Available      {'True' if deployment['available'] else 'False'}   MinimumReplicasAvailable",
            "Events:",
        ]
        lines.extend("  " + event for event in events)
        return CommandResult(0, "\n".join(lines) + "\n", "", "supported", "kubectl.describe.deployments")
    if kind == "replicasets":
        replicaset = _find_named(resources["replicasets"], name)
        if replicaset is None:
            return _not_found("replicasets", name)
        return CommandResult(
            0,
            (
                f"Name:           {replicaset['name']}\n"
                f"Namespace:      {state.namespace}\n"
                f"Controlled By:  Deployment/{replicaset['owner']}\n"
                f"Replicas:       {replicaset['ready']} ready / {replicaset['desired']} desired\n"
            ),
            "",
            "supported",
            "kubectl.describe.replicasets",
        )
    if kind == "daemonsets":
        daemonset = _find_named(resources["daemonsets"], name)
        if daemonset is None:
            return _not_found("daemonsets", name)
        return CommandResult(
            0,
            (
                f"Name:           {daemonset['name']}\n"
                f"Namespace:      {state.namespace}\n"
                f"Node Selector:  {daemonset['node_selector']}\n"
                f"Desired:        {daemonset['desired']}\n"
                f"Ready:          {daemonset['ready']}\n"
            ),
            "",
            "supported",
            "kubectl.describe.daemonsets",
        )
    if kind == "services":
        service = _find_named(resources["services"], name)
        if service is None:
            return _not_found("services", name)
        endpoint = _find_named(resources["endpoints"], name)
        lines = [
            f"Name:              {service['name']}",
            f"Namespace:         {state.namespace}",
            f"Type:              {service['type']}",
            f"IP:                {service['cluster_ip']}",
            f"Port:              {service['ports']}",
            f"Endpoints:         {endpoint['endpoints'] if endpoint else '<none>'}",
        ]
        return CommandResult(0, "\n".join(lines) + "\n", "", "supported", "kubectl.describe.services")
    if kind == "endpoints":
        endpoint = _find_named(resources["endpoints"], name)
        if endpoint is None:
            return _not_found("endpoints", name)
        return CommandResult(
            0,
            (
                f"Name:       {endpoint['name']}\n"
                f"Namespace:  {state.namespace}\n"
                f"Endpoints:  {endpoint['endpoints'] or '<none>'}\n"
            ),
            "",
            "supported",
            "kubectl.describe.endpoints",
        )
    if kind == "endpointslices":
        endpointslice = _find_named(resources["endpointslices"], name)
        if endpointslice is None:
            return _not_found("endpointslices", name)
        return CommandResult(
            0,
            (
                f"Name:          {endpointslice['name']}\n"
                f"Namespace:     {state.namespace}\n"
                f"Service:       {endpointslice['service']}\n"
                f"Address Type:  {endpointslice['address_type']}\n"
                f"Endpoints:     {endpointslice['endpoints']}\n"
            ),
            "",
            "supported",
            "kubectl.describe.endpointslices",
        )
    if kind == "hpa":
        hpa = _find_named(resources["hpa"], name)
        if hpa is None:
            return _not_found("horizontalpodautoscalers", name)
        return CommandResult(
            0,
            (
                f"Name:         {hpa['name']}\n"
                f"Namespace:    {state.namespace}\n"
                f"Reference:    {hpa['reference']}\n"
                f"Targets:      {hpa['targets']}\n"
                f"Replicas:     {hpa['replicas']}\n"
            ),
            "",
            "supported",
            "kubectl.describe.hpa",
        )
    if kind == "nodes":
        node = _find_named(resources["nodes"], name)
        if node is None:
            return _not_found("nodes", name)
        lines = [
            f"Name:               {node['name']}",
            f"Roles:              {node['roles']}",
            f"Status:             {node['status']}",
            "Conditions:",
            f"  Ready             {node['status'] == 'Ready'}",
            "Allocated resources:",
            f"  cpu               {node['cpu_pct']}%",
            f"  memory            {node['memory_pct']}%",
        ]
        return CommandResult(0, "\n".join(lines) + "\n", "", "supported", "kubectl.describe.nodes")
    if kind == "pvc":
        pvc = _find_named(resources["pvc"], name)
        if pvc is None:
            return _not_found("persistentvolumeclaims", name)
        lines = [
            f"Name:          {pvc['name']}",
            f"Namespace:     {state.namespace}",
            f"Status:        {pvc['status']}",
            f"Capacity:      {pvc['capacity']}",
            f"Used:          {pvc['used_pct']}%",
            "Events:",
            "  Warning VolumePressure database write volume approaching capacity"
            if pvc["used_pct"] >= 90 else "  Normal Bound volume attached",
        ]
        return CommandResult(0, "\n".join(lines) + "\n", "", "supported", "kubectl.describe.pvc")
    if kind == "statefulsets":
        statefulset = _find_named(resources["statefulsets"], name)
        if statefulset is None:
            return _not_found("statefulsets", name)
        return CommandResult(
            0,
            f"Name:       {statefulset['name']}\nNamespace:  {state.namespace}\nPods Status: {statefulset['ready']}\n",
            "",
            "supported",
            "kubectl.describe.statefulsets",
        )
    if kind == "configmaps":
        configmap = _find_named(resources["configmaps"], name)
        if configmap is None:
            return _not_found("configmaps", name)
        lines = [
            f"Name:      {configmap['name']}",
            f"Namespace: {state.namespace}",
            "Data",
        ]
        lines.extend(f"  {key}: {value}" for key, value in configmap["keys"].items())
        return CommandResult(0, "\n".join(lines) + "\n", "", "supported", "kubectl.describe.configmaps")
    if kind == "secrets":
        secret = _find_named(resources["secrets"], name)
        if secret is None:
            return _not_found("secrets", name)
        return CommandResult(
            0,
            (
                f"Name:      {secret['name']}\n"
                f"Namespace: {state.namespace}\n"
                f"Type:      {secret['type']}\n"
                f"Data:      {secret['data']}\n"
            ),
            "",
            "supported",
            "kubectl.describe.secrets",
        )
    if kind == "jobs":
        job = _find_named(resources["jobs"], name)
        if job is None:
            return _not_found("jobs", name)
        return CommandResult(
            0,
            (
                f"Name:        {job['name']}\n"
                f"Namespace:   {state.namespace}\n"
                f"Completions: {job['completions']}\n"
                f"Duration:    {job['duration']}\n"
            ),
            "",
            "supported",
            "kubectl.describe.jobs",
        )
    if kind == "cronjobs":
        cronjob = _find_named(resources["cronjobs"], name)
        if cronjob is None:
            return _not_found("cronjobs", name)
        return CommandResult(
            0,
            (
                f"Name:           {cronjob['name']}\n"
                f"Namespace:      {state.namespace}\n"
                f"Schedule:       {cronjob['schedule']}\n"
                f"Suspend:        {cronjob['suspend']}\n"
                f"Active Jobs:    {cronjob['active']}\n"
                f"Last Schedule:  {cronjob['last_schedule']}\n"
            ),
            "",
            "supported",
            "kubectl.describe.cronjobs",
        )
    if kind == "serviceaccounts":
        serviceaccount = _find_named(resources["serviceaccounts"], name)
        if serviceaccount is None:
            return _not_found("serviceaccounts", name)
        return CommandResult(
            0,
            (
                f"Name:      {serviceaccount['name']}\n"
                f"Namespace: {state.namespace}\n"
                f"Secrets:   {serviceaccount['secrets']}\n"
            ),
            "",
            "supported",
            "kubectl.describe.serviceaccounts",
        )
    if kind == "ingress":
        ingress = _find_named(resources["ingress"], name)
        if ingress is None:
            return _not_found("ingress", name)
        return CommandResult(
            0,
            (
                f"Name:      {ingress['name']}\n"
                f"Namespace: {state.namespace}\n"
                f"Class:     {ingress['class']}\n"
                f"Hosts:     {ingress['hosts']}\n"
                f"Address:   {ingress['address']}\n"
            ),
            "",
            "supported",
            "kubectl.describe.ingress",
        )
    if kind == "namespaces":
        namespace = _find_named(resources["namespaces"], name)
        if namespace is None:
            return _not_found("namespaces", name)
        return CommandResult(
            0,
            f"Name:   {namespace['name']}\nStatus: {namespace['status']}\n",
            "",
            "supported",
            "kubectl.describe.namespaces",
        )
    return _unsupported(parsed, f"kubectl describe {kind}")


def _logs_uses_selector(parsed: ParsedCommand) -> bool:
    return bool(parsed.flags.get("-l") or parsed.flags.get("--selector"))


def _render_logs_command(state: SimulationState, parsed: ParsedCommand) -> CommandResult:
    container = _logs_container_name(parsed)
    if _logs_has_container_flag(parsed) and not container:
        return CommandResult(
            1,
            "",
            "error: -c/--container requires a container name\n",
            "partial",
            "kubectl.logs.container",
        )
    pods = _logs_target_pods(state, parsed)
    if container:
        for pod in pods:
            if container != pod["component"]:
                return CommandResult(
                    1,
                    "",
                    f'error: container "{container}" is not valid for pod "{pod["name"]}"\n',
                    "partial",
                    "kubectl.logs.container",
                )
    since_time = _logs_since_time(parsed)
    if isinstance(since_time, str):
        return CommandResult(
            1,
            "",
            f'error: invalid --since-time value "{since_time}"\n',
            "partial",
            "kubectl.logs.since-time",
        )
    tail_limit = _logs_tail_limit(parsed)
    if isinstance(tail_limit, str):
        return CommandResult(
            1,
            "",
            f'error: invalid --tail value "{tail_limit}"\n',
            "partial",
            "kubectl.logs.tail",
        )
    rule_id = (
        "kubectl.logs.selector"
        if _logs_uses_selector(parsed) and not parsed.resource_name
        else "kubectl.logs.pod"
    )
    return CommandResult(
        0,
        _render_logs(state, parsed, pods=pods, since_time=since_time, tail_limit=tail_limit),
        "",
        "supported",
        rule_id,
    )


def _logs_target_pods(state: SimulationState, parsed: ParsedCommand) -> list[dict[str, Any]]:
    if parsed.resource_name:
        component = _component_from_name(parsed.resource_name, state.components) or parsed.resource_name
        return [{"name": parsed.resource_name, "component": component}]
    resources = resource_snapshot(state)
    return _filter_snapshot_rows("pods", resources["pods"], parsed)


def _logs_container_name(parsed: ParsedCommand) -> str:
    return str(parsed.flags.get("-c") or parsed.flags.get("--container") or "")


def _logs_has_container_flag(parsed: ParsedCommand) -> bool:
    return "-c" in parsed.flags or "--container" in parsed.flags


def _logs_since_time(parsed: ParsedCommand) -> _dt.datetime | str | None:
    raw = parsed.flags.get("--since-time")
    if raw is None:
        return None
    with contextlib.suppress(ValueError):
        return _parse_user_timestamp(str(raw))
    return str(raw)


def _logs_tail_limit(parsed: ParsedCommand) -> int | None | str:
    raw = parsed.flags.get("--tail")
    if raw is None:
        return 20
    with contextlib.suppress(ValueError):
        value = int(str(raw))
        return None if value < 0 else value
    return str(raw)


def _render_logs(
    state: SimulationState,
    parsed: ParsedCommand,
    *,
    pods: list[dict[str, Any]] | None = None,
    since_time: _dt.datetime | None = None,
    tail_limit: int | None = 20,
) -> str:
    target_pods = pods if pods is not None else _logs_target_pods(state, parsed)
    log_time = state.clock.now()
    if since_time is not None and since_time > log_time:
        return ""
    now = _format_dt(log_time)
    rendered: list[str] = []
    for pod in target_pods:
        rendered.extend(_render_pod_logs(state, parsed, pod, timestamp=now, tail_limit=tail_limit))
    return "".join(rendered)


def _render_pod_logs(
    state: SimulationState,
    parsed: ParsedCommand,
    pod: dict[str, Any],
    *,
    timestamp: str,
    tail_limit: int | None,
) -> list[str]:
    component = pod["component"]
    lines: list[str] = []
    for profile in state.profiles():
        if component in profile.affected_components:
            lines.extend(profile.logs)
    if not lines:
        lines = [
            f"{component} health probe ok",
            f"{component} processed request batch without anomaly",
        ]
    prefix = ""
    if parsed.flags.get("--prefix"):
        container = _logs_container_name(parsed) or component
        prefix = f"{pod['name']}/{container} "
    if parsed.flags.get("--previous") or parsed.flags.get("-p"):
        prefix += "previous "
    if tail_limit is not None:
        lines = lines[-tail_limit:] if tail_limit else []
    return [f"{timestamp} {prefix}{line}\n" for line in lines]


def _render_top(state: SimulationState, kind: str) -> str:
    resources = resource_snapshot(state)
    if kind == "pods":
        return _table(["NAME", "CPU(cores)", "MEMORY(bytes)"], [
            [pod["name"], f"{pod['cpu_m']}m", f"{pod['memory_mi']}Mi"]
            for pod in resources["pods"]
        ])
    return _table(["NAME", "CPU(cores)", "CPU%", "MEMORY(bytes)", "MEMORY%"], [
        [node["name"], f"{node['cpu_m']}m", f"{node['cpu_pct']}%", f"{node['memory_mi']}Mi", f"{node['memory_pct']}%"]
        for node in resources["nodes"]
    ])


def _render_kubectl_version() -> str:
    return (
        f"Client Version: {_K8S_ADVERTISED_TAG}\n"
        "Kustomize Version: v5.0.4\n"
        f"Server Version: {_K8S_ADVERTISED_GIT_VERSION}\n"
    )


def _render_kubectl_api_versions() -> str:
    versions = [
        "v1",
        "apps/v1",
        "autoscaling/v2",
        "batch/v1",
        "discovery.k8s.io/v1",
        "networking.k8s.io/v1",
        "metrics.k8s.io/v1beta1",
        "authorization.k8s.io/v1",
    ]
    return "\n".join(versions) + "\n"


def _render_kubectl_api_resources() -> str:
    rows = [
        ["pods", "po", "true", "Pod"],
        ["services", "svc", "true", "Service"],
        ["configmaps", "cm", "true", "ConfigMap"],
        ["secrets", "", "true", "Secret"],
        ["endpoints", "ep", "true", "Endpoints"],
        ["serviceaccounts", "sa", "true", "ServiceAccount"],
        ["nodes", "no", "false", "Node"],
        ["deployments", "deploy", "true", "Deployment"],
        ["replicasets", "rs", "true", "ReplicaSet"],
        ["daemonsets", "ds", "true", "DaemonSet"],
        ["statefulsets", "sts", "true", "StatefulSet"],
        ["horizontalpodautoscalers", "hpa", "true", "HorizontalPodAutoscaler"],
        ["jobs", "", "true", "Job"],
        ["cronjobs", "cj", "true", "CronJob"],
        ["ingresses", "ing", "true", "Ingress"],
        ["endpointslices", "", "true", "EndpointSlice"],
    ]
    return _table(["NAME", "SHORTNAMES", "NAMESPACED", "KIND"], rows)


def _render_kubectl_cluster_info() -> str:
    return (
        "Kubernetes control plane is running at http://127.0.0.1:8088\n"
        "AMC simulator debug console is running at http://127.0.0.1:8088/debug\n"
    )
