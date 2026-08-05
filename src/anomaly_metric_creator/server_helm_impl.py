"""Helm command renderers, release/notes model, and Secret encoding.

Extracted verbatim from ``server_ops.py`` (epic ``07-06-server-ops-decomposition``
step 3). This is a one-way leaf: it imports only stdlib and already-extracted
lower leaves; nothing here imports ``server_ops`` at runtime. ``server_ops``
re-imports every name below at each block's original position so the historic
``server_ops.<name>`` surface (``server.py`` alias block, the ``server_helm`` /
``server_commands`` facades, ``server_mcp``, and the Helm Secret REST objects)
resolves unchanged.
"""
from __future__ import annotations

import base64
import gzip
import json
from typing import TYPE_CHECKING, Any

from .server_command_render import (
    CommandResult,
    _exposed_active_scenarios,
    _is_dry_run,
    _table,
    _unsupported,
)
from .server_k8s_objects import _k8s_metadata, _k8s_timestamp
from .server_mutations import _format_dt
from .server_ops_parse import ParsedCommand, _first_flag_value, _flag_values
from .server_ops_support import DEFAULT_CHART, DEFAULT_RELEASE

if TYPE_CHECKING:
    from .server_ops import SimulationState

__all__ = [
    "_render_helm",
    "_render_helm_list",
    "_render_helm_status",
    "_render_helm_history",
    "_render_helm_env",
    "_render_helm_get",
    "_render_helm_test",
    "_render_helm_install",
    "_render_helm_upgrade",
    "_helm_value_overrides",
    "_helm_operation_note",
    "_render_helm_rollback",
    "_helm_release",
    "_helm_notes",
    "_helm_current_description",
    "_helm_secret_objects",
    "_helm_release_revisions",
    "_helm_secret_object",
    "_helm_encoded_release_data",
    "_helm_release_payload",
]


def _render_helm(state: SimulationState, parsed: ParsedCommand) -> CommandResult:
    if parsed.verb == "version":
        return CommandResult(0, "version.BuildInfo{Version:\"v4.2.2\", GitCommit:\"simulated\"}\n", "", "supported", "helm.version")
    if parsed.verb == "env":
        return CommandResult(0, _render_helm_env(), "", "supported", "helm.env")
    if parsed.verb == "template":
        return CommandResult(0, _render_helm_get(state, "manifest"), "", "supported", "helm.template")
    if parsed.verb == "list":
        return CommandResult(0, _render_helm_list(state), "", "supported", "helm.list")
    if parsed.verb == "status":
        return CommandResult(0, _render_helm_status(state), "", "supported", "helm.status")
    if parsed.verb == "history":
        return CommandResult(0, _render_helm_history(state), "", "supported", "helm.history")
    if parsed.verb == "get":
        if parsed.resource_kind in {"values", "manifest", "notes", "all", "hooks"}:
            return CommandResult(
                0, _render_helm_get(state, parsed.resource_kind), "", "supported",
                f"helm.get.{parsed.resource_kind}",
            )
        return _unsupported(parsed, f"helm get {parsed.resource_kind or '<missing-kind>'}")
    if parsed.verb == "test":
        return CommandResult(0, _render_helm_test(state), "", "supported", "helm.test")
    if parsed.verb == "install":
        return CommandResult(0, _render_helm_install(state, parsed), "", "supported", "helm.install")
    if parsed.verb == "upgrade":
        return CommandResult(0, _render_helm_upgrade(state, parsed), "", "supported", "helm.upgrade")
    if parsed.verb == "rollback":
        return CommandResult(0, _render_helm_rollback(state, parsed), "", "supported", "helm.rollback")
    if parsed.verb == "uninstall":
        release = parsed.resource_name or DEFAULT_RELEASE
        now = state.clock.now()
        revisions = [
            {**revision, "status": "uninstalled" if revision["status"] == "deployed" else revision["status"]}
            for revision in _helm_release_revisions(state)
        ]
        state.mutations.set_revisions(revisions, now=now, uninstalled=True)
        state.mutations.record_event(
            "Normal",
            "HelmUninstall",
            f"release/{release}",
            f"release {release} uninstalled from simulator state",
            now,
        )
        return CommandResult(
            0,
            f"release \"{release}\" uninstalled\n",
            "",
            "supported",
            "helm.uninstall",
        )
    return _unsupported(parsed, f"helm {parsed.verb or '<missing-verb>'}")


def _render_helm_list(state: SimulationState) -> str:
    with state.mutations.lock:
        if state.mutations.release.uninstalled:
            return _table(
                ["NAME", "NAMESPACE", "REVISION", "UPDATED", "STATUS", "CHART", "APP VERSION"],
                [],
            )
    release = _helm_release(state)
    return _table(["NAME", "NAMESPACE", "REVISION", "UPDATED", "STATUS", "CHART", "APP VERSION"], [[
        release["name"], release["namespace"], str(release["revision"]),
        release["updated"], release["status"], release["chart"], release["app_version"],
    ]])


def _render_helm_status(state: SimulationState) -> str:
    release = _helm_release(state)
    return (
        f"NAME: {release['name']}\n"
        f"LAST DEPLOYED: {release['updated']}\n"
        f"NAMESPACE: {release['namespace']}\n"
        f"STATUS: {release['status']}\n"
        f"REVISION: {release['revision']}\n"
        f"NOTES:\n{_helm_notes(state)}\n"
    )


def _render_helm_history(state: SimulationState) -> str:
    rows = []
    now = state.clock.now()
    for revision in _helm_release_revisions(state):
        version = int(revision["version"])
        if version == 1:
            updated = "2026-03-01 00:00:00"
        elif version == 2:
            updated = "2026-03-08 00:00:00"
        else:
            updated = _format_dt(now)
        rows.append([
            str(version),
            updated,
            str(revision["status"]),
            DEFAULT_CHART,
            str(revision["description"]),
        ])
    return _table(["REVISION", "UPDATED", "STATUS", "CHART", "DESCRIPTION"], rows)


def _render_helm_env() -> str:
    return (
        "HELM_BIN=\"helm\"\n"
        "HELM_CACHE_HOME=\"/tmp/amc/helm/cache\"\n"
        "HELM_CONFIG_HOME=\"/tmp/amc/helm/config\"\n"
        "HELM_DATA_HOME=\"/tmp/amc/helm/data\"\n"
        "HELM_NAMESPACE=\"saas-prod\"\n"
        "HELM_DRIVER=\"secrets\"\n"
    )


def _render_helm_get(state: SimulationState, kind: str) -> str:
    if kind == "values":
        with state.mutations.lock:
            values = dict(state.mutations.release.values)
        value_lines = "".join(
            f"{key}: {value}\n"
            for key, value in sorted(values.items())
        )
        return (
            "replicaCount: 3\n"
            f"namespace: {state.namespace}\n"
            "observability:\n"
            "  otel: true\n"
            f"scenarios: {json.dumps(list(_exposed_active_scenarios(state)))}\n"
            + value_lines
        )
    if kind == "manifest":
        deployments = "\n".join(
            f"---\napiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: {component}\n"
            f"  namespace: {state.namespace}\n"
            for component in state.components
        )
        return deployments or "---\n"
    if kind == "hooks":
        return "HOOKS:\n(no hooks defined for simulated-saas)\n"
    if kind == "all":
        return (
            "COMPUTED VALUES:\n"
            + _render_helm_get(state, "values")
            + "\nMANIFEST:\n"
            + _render_helm_get(state, "manifest")
            + "\nNOTES:\n"
            + _render_helm_get(state, "notes")
        )
    return _helm_notes(state) + "\n"


def _render_helm_test(state: SimulationState) -> str:
    rows = [["simulated-saas-connectivity", "Succeeded", _format_dt(state.clock.now())]]
    if "deploy_bad_canary_rollback" in state.active_scenarios:
        rows.append(["simulated-saas-canary", "SucceededAfterRollback", _format_dt(state.clock.now())])
    return _table(["NAME", "STATUS", "LAST RUN"], rows)


def _render_helm_install(state: SimulationState, parsed: ParsedCommand) -> str:
    release = parsed.resource_name or DEFAULT_RELEASE
    now = state.clock.now()
    values = _helm_value_overrides(parsed)
    dry_run = _is_dry_run(parsed)
    if not dry_run:
        revisions = [{
            "version": 1,
            "status": "deployed",
            "description": f"Install applied to {release}",
        }]
        state.mutations.set_revisions(revisions, now=now, uninstalled=False)
        if values:
            state.mutations.set_release_values(values, now=now)
        state.mutations.record_event(
            "Normal",
            "HelmInstall",
            f"release/{release}",
            f"release {release} installed by simulator command",
            now,
        )
    note = _helm_operation_note(parsed, dry_run=dry_run, values=values, reset=False)
    return (
        f"NAME: {release}\n"
        f"LAST DEPLOYED: {_format_dt(now)}\n"
        f"NAMESPACE: {state.namespace}\n"
        "STATUS: deployed\n"
        "REVISION: 1\n"
        f"{note}"
    )


def _render_helm_upgrade(state: SimulationState, parsed: ParsedCommand) -> str:
    release = parsed.resource_name or DEFAULT_RELEASE
    dry_run = _is_dry_run(parsed)
    mode = "dry run" if dry_run else "simulated"
    current = _helm_release_revisions(state)
    values = _helm_value_overrides(parsed)
    reset = "--reset-values" in parsed.flags
    if not dry_run:
        now = state.clock.now()
        revisions = [
            {**revision, "status": "superseded" if revision["status"] == "deployed" else revision["status"]}
            for revision in current
        ]
        revisions.append({
            "version": int(revisions[-1]["version"]) + 1 if revisions else 1,
            "status": "deployed",
            "description": f"Upgrade applied to {release}",
        })
        state.mutations.set_revisions(revisions, now=now, uninstalled=False)
        if reset:
            state.mutations.replace_release_values(values, now=now)
        elif values:
            state.mutations.set_release_values(values, now=now)
        state.mutations.record_event(
            "Normal",
            "HelmUpgrade",
            f"release/{release}",
            f"release {release} upgraded by simulator command",
            now,
        )
    note = _helm_operation_note(parsed, dry_run=dry_run, values=values, reset=reset)
    return (
        f"Release \"{release}\" has been upgraded ({mode}).\n"
        f"NAMESPACE: {state.namespace}\n"
        f"STATUS: {_helm_release(state)['status']}\n"
        f"{note}"
    )


def _helm_value_overrides(parsed: ParsedCommand) -> dict[str, str]:
    values: dict[str, str] = {}
    for flag in ("--set", "--set-string"):
        for raw in _flag_values(parsed.flags, flag):
            for item in raw.split(","):
                key, _, value = item.partition("=")
                if key:
                    values[key] = value or "true"
    value_files = _flag_values(parsed.flags, "--values", "-f")
    if value_files:
        values["values_file"] = value_files[-1]
        values["values_files"] = ",".join(value_files)
    return values


def _helm_operation_note(
    parsed: ParsedCommand,
    *,
    dry_run: bool,
    values: dict[str, str],
    reset: bool,
) -> str:
    notes = []
    if dry_run:
        notes.append("NOTE: simulator release state not changed during dry run.")
    else:
        notes.append("NOTE: simulator release state updated.")
    if "--reuse-values" in parsed.flags and not reset:
        notes.append("NOTE: simulator reused existing release values before applying overrides.")
    if reset:
        action = "would reset" if dry_run else "reset"
        notes.append(f"NOTE: simulator {action} release values before applying overrides.")
    if values.get("values_files"):
        notes.append(f"NOTE: simulator recorded values files: {values['values_files']}.")
    if "--wait" in parsed.flags:
        timeout = _first_flag_value(parsed.flags, "--timeout", default="default timeout")
        notes.append(f"NOTE: simulator wait completed before {timeout}.")
    if "--atomic" in parsed.flags:
        notes.append("NOTE: simulator atomic rollback was not needed.")
    return "\n".join(notes) + "\n"


def _render_helm_rollback(state: SimulationState, parsed: ParsedCommand) -> str:
    release = parsed.resource_name or DEFAULT_RELEASE
    revision = parsed.positionals[2] if len(parsed.positionals) > 2 else "previous"
    now = state.clock.now()
    current = _helm_release_revisions(state)
    revisions = [
        {**item, "status": "superseded" if item["status"] == "deployed" else item["status"]}
        for item in current
    ]
    revisions.append({
        "version": int(revisions[-1]["version"]) + 1 if revisions else 1,
        "status": "deployed",
        "description": f"Rollback to revision {revision}",
    })
    state.mutations.set_revisions(revisions, now=now, uninstalled=False)
    state.mutations.record_event(
        "Normal",
        "HelmRollback",
        f"release/{release}",
        f"release {release} rolled back to revision {revision}",
        now,
    )
    return (
        f"Rollback was a success for release \"{release}\" to revision {revision}.\n"
        f"NAMESPACE: {state.namespace}\n"
        "NOTE: simulator release state updated.\n"
    )


def _helm_release(state: SimulationState) -> dict[str, Any]:
    revisions = _helm_release_revisions(state)
    current = revisions[-1]
    return {
        "name": DEFAULT_RELEASE,
        "namespace": state.namespace,
        "revision": int(current["version"]),
        "updated": _format_dt(state.clock.now()),
        "status": str(current["status"]),
        "chart": DEFAULT_CHART,
        "app_version": "0.3.0",
    }


def _helm_notes(state: SimulationState) -> str:
    if not state.profiles():
        return "Run kubectl get pods -n saas-prod for workload state."
    return "\n".join(
        f"- {profile.helm_notes}"
        for profile in state.profiles()
    )


def _helm_current_description(state: SimulationState) -> str:
    summaries = [profile.summary for profile in state.profiles()]
    if not summaries:
        return "Baseline config"
    description = "; ".join(summaries)
    if len(description) > 160:
        description = description[:157].rstrip() + "..."
    return description


def _helm_secret_objects(state: SimulationState) -> list[dict[str, Any]]:
    return [
        _helm_secret_object(state, revision)
        for revision in _helm_release_revisions(state)
    ]


def _helm_release_revisions(state: SimulationState) -> list[dict[str, Any]]:
    base = [
        {"version": 1, "status": "superseded", "description": "Install complete"},
        {"version": 2, "status": "deployed", "description": "Baseline config"},
    ]
    if "deploy_bad_canary_rollback" in state.active_scenarios:
        base = [
            {"version": 1, "status": "superseded", "description": "Install complete"},
            {"version": 2, "status": "superseded", "description": "Baseline config"},
            {"version": 3, "status": "failed", "description": "Canary readiness failed"},
            {"version": 4, "status": "deployed", "description": "Rollback to revision 2"},
        ]
    elif state.profiles():
        base = [
            {"version": 1, "status": "superseded", "description": "Install complete"},
            {"version": 2, "status": "superseded", "description": "Baseline config"},
            {"version": 3, "status": "deployed", "description": _helm_current_description(state)},
        ]
    return state.mutations.current_revisions(base)


def _helm_secret_object(state: SimulationState, revision: dict[str, Any]) -> dict[str, Any]:
    version = int(revision["version"])
    status = str(revision["status"])
    name = f"sh.helm.release.v1.{DEFAULT_RELEASE}.v{version}"
    labels = {
        "owner": "helm",
        "name": DEFAULT_RELEASE,
        "status": status,
        "version": str(version),
    }
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": _k8s_metadata(
            state,
            name,
            namespace=state.namespace,
            labels=labels,
            annotations={
                "meta.helm.sh/release-name": DEFAULT_RELEASE,
                "meta.helm.sh/release-namespace": state.namespace,
            },
        ),
        "type": "helm.sh/release.v1",
        "data": {
            "release": _helm_encoded_release_data(state, revision),
        },
    }


def _helm_encoded_release_data(state: SimulationState, revision: dict[str, Any]) -> str:
    release = _helm_release_payload(state, revision)
    compressed = gzip.compress(json.dumps(release, sort_keys=True).encode("utf-8"))
    helm_encoded = base64.b64encode(compressed)
    return base64.b64encode(helm_encoded).decode("ascii")


def _helm_release_payload(state: SimulationState, revision: dict[str, Any]) -> dict[str, Any]:
    chart_version = DEFAULT_CHART.removeprefix(DEFAULT_RELEASE + "-")
    status = str(revision["status"])
    return {
        "name": DEFAULT_RELEASE,
        "info": {
            "first_deployed": "2026-03-01T00:00:00Z",
            "last_deployed": _k8s_timestamp(state.clock.now()),
            "deleted": "0001-01-01T00:00:00Z",
            "description": revision["description"],
            "status": status,
            "notes": _helm_notes(state),
        },
        "chart": {
            "metadata": {
                "name": DEFAULT_RELEASE,
                "version": chart_version,
                "appVersion": "0.3.0",
                "apiVersion": "v2",
                "description": "Simulated SaaS incident workload",
                "type": "application",
            },
            "templates": [],
            "values": {},
            "files": [],
            "schema": None,
        },
        "config": {
            "replicaCount": 3,
            "namespace": state.namespace,
            "observability": {"otel": True},
            "scenarios": list(_exposed_active_scenarios(state)),
        },
        "manifest": _render_helm_get(state, "manifest"),
        "hooks": [],
        "version": int(revision["version"]),
        "namespace": state.namespace,
        "labels": {},
    }
