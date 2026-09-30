"""Workload-operation kubectl renderers for the serve-mode simulator.

This leaf owns ``kubectl scale``, ``delete``, the six ``rollout``
subcommands (status, history, restart, pause, resume, undo), ``wait``,
``exec`` and ``port-forward``, plus the rollout and replica helpers they
share. The mutating renderers resolve targets against the overlay-aware
``resource_snapshot()`` from ``server_ops_snapshot`` before any write; a
test that stubs the snapshot for these renderers patches it here.
``server_ops`` re-imports every name at its original position, so the
historic ``server_ops.<name>`` and ``server.<name>`` surfaces stay stable.
This module never imports ``server_ops`` at runtime; ``SimulationState`` is
a type-only import.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .server_command_render import CommandResult, _exposed_active_scenarios, _not_found, _table
from .server_mutations import _resource_prefix
from .server_ops_parse import _KIND_ALIASES, _first_flag_value, _normalize_kind
from .server_ops_render_manifest import _mutation_snapshot_kind
from .server_ops_snapshot import _component_health, _replica_count, resource_snapshot
from .server_ops_support import _component_from_name, _find_named

if TYPE_CHECKING:
    from .server_ops import SimulationState
    from .server_ops_parse import ParsedCommand


def _normalized_resource_prefix(kind: str) -> str:
    normalized = _mutation_snapshot_kind(kind) or _normalize_kind(kind)
    return _resource_prefix(normalized or kind or "resource")


def _render_rollout_status(state: SimulationState, parsed: ParsedCommand) -> str:
    component = _rollout_component(parsed)
    if "deploy_bad_canary_rollback" in state.active_scenarios and component == "apigateway":
        return (
            "deployment \"apigateway\" successfully rolled out\n"
            "note: release was rolled back from failed canary revision\n"
        )
    health = _component_health(state, component)
    rollout_notes = _component_rollout_notes(state, component)
    if health["deployment_status"] == "RolledBack":
        output = f"deployment \"{component}\" successfully rolled out\n"
        output += "note: deployment was rolled back by simulator command\n"
        if rollout_notes:
            output += "\n".join(f"note: {note}" for note in rollout_notes) + "\n"
        return output
    if health["deployment_status"] != "Healthy":
        output = f"waiting for deployment \"{component}\" rollout to finish: {health['deployment_status']}\n"
        if rollout_notes:
            output += "\n".join(f"note: {note}" for note in rollout_notes) + "\n"
        return output
    output = f"deployment \"{component}\" successfully rolled out\n"
    if rollout_notes:
        output += "\n".join(f"note: {note}" for note in rollout_notes) + "\n"
    return output


def _render_rollout_history(state: SimulationState, parsed: ParsedCommand) -> str:
    component = _rollout_component(parsed)
    rows = [["1", "simulated-saas-0.2.0", "baseline deployment"]]
    if "deploy_bad_canary_rollback" in state.active_scenarios and component == "apigateway":
        rows.extend([
            ["2", "simulated-saas-0.3.0-canary", "canary readiness failed"],
            ["3", "simulated-saas-0.3.0", "rollback to stable revision"],
        ])
    else:
        description = "current deployment"
        rollout_notes = _component_rollout_notes(state, component)
        if rollout_notes:
            description = "; ".join(rollout_notes)
        rows.append(["2", "simulated-saas-0.3.0", description])
    return f"deployment.apps/{component}\n" + _table(["REVISION", "CHANGE-CAUSE", "DESCRIPTION"], rows)


def _render_rollout_restart(state: SimulationState, parsed: ParsedCommand) -> str:
    component = _rollout_component(parsed)
    now = state.clock.now()
    state.mutations.set_workload(
        component,
        now=now,
        deployment_status="Restarting",
        pod_status="Running",
        restarts_delta=1,
    )
    state.mutations.record_event(
        "Normal",
        "RolloutRestart",
        f"deployment/{component}",
        f"deployment {component} restarted by simulator command",
        now,
    )
    return f"deployment.apps/{component} restarted\n"


def _render_rollout_pause(state: SimulationState, parsed: ParsedCommand) -> str:
    component = _rollout_component(parsed)
    now = state.clock.now()
    replicas = _replica_count(state, component)
    state.mutations.set_workload(
        component,
        now=now,
        ready_replicas=replicas,
        deployment_status="Paused",
        pod_status="Running",
    )
    state.mutations.record_event(
        "Normal",
        "RolloutPaused",
        f"deployment/{component}",
        f"deployment {component} rollout paused by simulator command",
        now,
    )
    return f"deployment.apps/{component} paused\n"


def _render_rollout_resume(state: SimulationState, parsed: ParsedCommand) -> str:
    component = _rollout_component(parsed)
    now = state.clock.now()
    replicas = _replica_count(state, component)
    state.mutations.set_workload(
        component,
        now=now,
        ready_replicas=replicas,
        deployment_status="Healthy",
        pod_status="Running",
    )
    state.mutations.record_event(
        "Normal",
        "RolloutResumed",
        f"deployment/{component}",
        f"deployment {component} rollout resumed by simulator command",
        now,
    )
    return f"deployment.apps/{component} resumed\n"


def _render_rollout_undo(state: SimulationState, parsed: ParsedCommand) -> str:
    component = _rollout_component(parsed)
    now = state.clock.now()
    replicas = _replica_count(state, component)
    revision = _rollout_undo_revision(parsed)
    state.mutations.set_workload(
        component,
        now=now,
        ready_replicas=replicas,
        deployment_status="RolledBack",
        pod_status="Running",
    )
    state.mutations.record_event(
        "Normal",
        "RolloutUndo",
        f"deployment/{component}",
        f"deployment {component} rolled back to {_rollout_revision_label(revision)} by simulator command",
        now,
    )
    suffix = f" to revision {revision}" if revision != "previous" else ""
    return f"deployment.apps/{component} rolled back{suffix}\n"


def _rollout_undo_revision(parsed: ParsedCommand) -> str:
    revision = _first_flag_value(parsed.flags, "--to-revision", default="previous").strip()
    if not revision or revision.startswith("-"):
        return "previous"
    return revision


def _rollout_revision_label(revision: str) -> str:
    if revision == "previous":
        return "previous revision"
    return f"revision {revision}"


def _rollout_component(parsed: ParsedCommand) -> str:
    component = parsed.resource_name or "apigateway"
    if component.startswith("deployment/"):
        component = component.split("/", 1)[1]
    return component


def _is_deployment_rollout_target(parsed: ParsedCommand) -> bool:
    return parsed.resource_kind == "deployments" and bool(parsed.resource_name)


def _render_scale(state: SimulationState, parsed: ParsedCommand) -> str | CommandResult:
    if parsed.resource_kind not in {"deployments", "deployment", "deploy", ""}:
        return f"{_normalized_resource_prefix(parsed.resource_kind)}/{parsed.resource_name} scaled\n"
    name = parsed.resource_name
    if not name:
        # A nameless scale used to default to apigateway and mutate its
        # workload — real kubectl rejects an empty resource name, and silently
        # scaling an arbitrary default pollutes the overlay (audit A-013).
        return CommandResult(
            1,
            "",
            "error: resource(s) were provided, but no name was specified\n",
            "supported",
            "kubectl.scale.usage",
        )
    # Resolve the target against the overlay-aware snapshot before any write —
    # the same order the API deployment-scale path enforces (audit A-013).
    if _find_named(resource_snapshot(state)["deployments"], name) is None:
        return _not_found("deployments", name)
    component = name
    replicas = _parsed_replicas(parsed)
    now = state.clock.now()
    state.mutations.set_workload(
        component,
        now=now,
        replicas=replicas,
        ready_replicas=replicas,
        deployment_status="Healthy" if replicas else "ScaledToZero",
        pod_status="Running",
    )
    state.mutations.record_event(
        "Normal",
        "ScalingReplicaSet",
        f"deployment/{component}",
        f"scaled deployment {component} to {replicas} replicas",
        now,
    )
    return f"deployment.apps/{component} scaled\n"


def _render_delete(state: SimulationState, parsed: ParsedCommand) -> str | CommandResult:
    kind = parsed.resource_kind
    name = parsed.resource_name
    now = state.clock.now()
    if kind in {"pods", "pod"} and name:
        # Resolve against the overlay-aware snapshot before deleting — a ghost
        # name used to record a phantom deletion in the overlay while the API
        # path 404'd (audit A-013). Same order the API pods-delete branch uses.
        if _find_named(resource_snapshot(state)["pods"], name) is None:
            return _not_found("pods", name)
        state.mutations.delete_pod(name, now=now)
        return f"pod \"{name}\" deleted\n"
    if kind in {"deployments", "deployment", "deploy"} and name:
        if _find_named(resource_snapshot(state)["deployments"], name) is None:
            return _not_found("deployments", name)
        state.mutations.set_workload(
            name,
            now=now,
            replicas=0,
            ready_replicas=0,
            deployment_status="Deleted",
            pod_status="Terminating",
            deleted=True,
        )
        state.mutations.record_event(
            "Normal",
            "Deleted",
            f"deployment/{name}",
            f"deployment {name} deleted from simulator state",
            now,
        )
        return f"deployment.apps \"{name}\" deleted\n"
    snapshot_kind = _mutation_snapshot_kind(kind)
    if snapshot_kind and name:
        # Generic modeled kind: mirror the API generic-delete existence guard
        # (``resource_snapshot(...).get(kind, [])``) so a ghost generic resource
        # 404s on both entry paths instead of recording a phantom delete.
        if _find_named(resource_snapshot(state).get(snapshot_kind, []), name) is None:
            return _not_found(snapshot_kind, name)
        state.mutations.delete_resource(snapshot_kind, name, now=now, namespace=parsed.namespace)
    prefix = _resource_prefix(snapshot_kind or _KIND_ALIASES.get(kind, kind) or "resource")
    return f"{prefix} \"{name}\" deleted\n"


def _parsed_replicas(parsed: ParsedCommand) -> int:
    value = parsed.flags.get("--replicas")
    if value is None:
        for token in parsed.positionals:
            if token.startswith("--replicas="):
                value = token.split("=", 1)[1]
                break
    try:
        return max(0, int(str(value)))
    except (TypeError, ValueError):
        return 1


def _render_wait(state: SimulationState, parsed: ParsedCommand) -> str:
    component = parsed.resource_name or "apigateway"
    health = _component_health(state, component)
    condition = str(parsed.flags.get("--for") or "condition=available")
    prefix = _normalized_resource_prefix(parsed.resource_kind)
    if health["deployment_status"] in {"Healthy", "RolledBack"}:
        return f"{prefix}/{component} condition met: {condition}\n"
    return f"{prefix}/{component} condition pending: {health['deployment_status']}\n"


def _render_exec(state: SimulationState, parsed: ParsedCommand) -> str:
    pod_name = parsed.resource_name
    component = _component_from_name(pod_name, state.components)
    if len(parsed.positionals) > 2 and "--" in parsed.positionals:
        command = " ".join(parsed.positionals[parsed.positionals.index("--") + 1:])
    else:
        command = " ".join(parsed.positionals[2:]) or "healthcheck"
    if any(token in command for token in {"env", "printenv"}):
        return (
            f"SERVICE_NAME={component}\n"
            f"NAMESPACE={state.namespace}\n"
            f"SCENARIOS={','.join(_exposed_active_scenarios(state))}\n"
        )
    if "curl" in command:
        return f"HTTP/1.1 200 OK\nx-amc-component: {component}\n\nok\n"
    return f"{pod_name}: simulated exec completed for `{command}`\n"


def _render_port_forward(parsed: ParsedCommand) -> str:
    port = parsed.positionals[2] if len(parsed.positionals) > 2 else "8080:8080"
    return (
        f"Forwarding from 127.0.0.1:{port.split(':', 1)[0]} -> {port.split(':')[-1]}\n"
        "Forwarding from [::1]:"
        f"{port.split(':', 1)[0]} -> {port.split(':')[-1]}\n"
        "simulator note: stream held open only in real kubectl; command API returns immediately\n"
    )


def _component_rollout_notes(state: SimulationState, component: str) -> list[str]:
    notes = []
    for profile in state.profiles():
        if component in profile.affected_components:
            notes.append(profile.rollout_note or profile.summary)
    return notes
