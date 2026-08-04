"""Client command parsing, fingerprinting, and flag redaction.

Stdlib-only leaf extracted from ``server_ops.py`` (epic
``07-06-server-ops-decomposition`` step 2). Owns the ``ParsedCommand``
return type, the flag/alias data tables, ``parse_command`` and its family
sub-parsers, and the fingerprint/redaction helpers. It never imports
``server_ops`` (strict one-way dependency); ``server_ops`` re-imports every
public name here at the position ``ParsedCommand`` originally held, so the
compatibility surface (``server.py``'s alias block, the ``server_commands.py``
facade, ``server_mcp.py`` imports) is unchanged.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .server_mutations import DEFAULT_NAMESPACE


@dataclass(frozen=True)
class ParsedCommand:
    raw_input: str
    argv: tuple[str, ...]
    family: str
    verb: str
    resource_kind: str
    resource_name: str
    namespace: str
    flags: dict[str, Any]
    positionals: tuple[str, ...]
    parse_error: str = ""


_VALUE_FLAGS = {
    "-n", "--namespace", "-o", "--output", "-l", "--selector",
    "--context", "--kubeconfig", "-c", "--container", "--tail", "--since",
    "--since-time", "--field-selector", "--sort-by", "--for", "--timeout", "--replicas",
    "--to-revision",
    "-f", "--filename", "--from-literal", "--from-file", "--image", "--schedule",
    "--set", "--set-string", "--values", "--api-version", "--patch", "--type",
}


_REPEATABLE_VALUE_FLAGS = {
    "-f", "--filename", "--from-literal", "--from-file", "--set", "--set-string", "--values",
}


_BOOL_FLAGS = {
    "-A", "--all-namespaces", "--previous", "-p", "--follow",
    "--prefix", "--watch", "-w", "--wide", "--show-labels", "--dry-run", "--install",
    "--atomic", "--debug", "--all", "--short", "--recursive", "--wait",
    "--reuse-values", "--reset-values", "--",
}


_SENSITIVE_FLAG_TOKENS = ("token", "password", "secret", "client-key")


_MODELED_FLAGS = {
    "namespace",
    "-A", "--all-namespaces",
    "-o", "--output",
    "-l", "--selector",
    "-c", "--container",
    "--tail", "--since", "--since-time", "--follow", "--prefix",
    "--previous", "-p",
    "--wide", "--show-labels",
    "--field-selector", "--sort-by", "--for", "--timeout",
    "--replicas", "--to-revision", "-f", "--filename", "--from-literal", "--from-file",
    "--image", "--schedule", "--set", "--set-string", "--values",
    "--dry-run", "--install", "--atomic", "--debug", "--all", "--short",
    "--recursive", "--api-version", "--patch", "--type", "--wait",
    "--reuse-values", "--reset-values", "--",
}


_KIND_ALIASES = {
    "all": "all",
    "ns": "namespaces",
    "namespace": "namespaces",
    "namespaces": "namespaces",
    "po": "pods",
    "pod": "pods",
    "pods": "pods",
    "cm": "configmaps",
    "configmap": "configmaps",
    "configmaps": "configmaps",
    "secret": "secrets",
    "secrets": "secrets",
    "rc": "replicationcontrollers",
    "replicationcontroller": "replicationcontrollers",
    "replicationcontrollers": "replicationcontrollers",
    "deploy": "deployments",
    "deployment": "deployments",
    "deployments": "deployments",
    "rs": "replicasets",
    "replicaset": "replicasets",
    "replicasets": "replicasets",
    "ds": "daemonsets",
    "daemonset": "daemonsets",
    "daemonsets": "daemonsets",
    "svc": "services",
    "service": "services",
    "services": "services",
    "ep": "endpoints",
    "endpoint": "endpoints",
    "endpoints": "endpoints",
    "endpointslice": "endpointslices",
    "endpointslices": "endpointslices",
    "events": "events",
    "event": "events",
    "hpa": "hpa",
    "horizontalpodautoscaler": "hpa",
    "horizontalpodautoscalers": "hpa",
    "job": "jobs",
    "jobs": "jobs",
    "cj": "cronjobs",
    "cronjob": "cronjobs",
    "cronjobs": "cronjobs",
    "sa": "serviceaccounts",
    "serviceaccount": "serviceaccounts",
    "serviceaccounts": "serviceaccounts",
    "node": "nodes",
    "nodes": "nodes",
    "no": "nodes",
    "pvc": "pvc",
    "pvcs": "pvc",
    "persistentvolumeclaim": "pvc",
    "persistentvolumeclaims": "pvc",
    "sts": "statefulsets",
    "statefulset": "statefulsets",
    "statefulsets": "statefulsets",
    "ing": "ingress",
    "ingress": "ingress",
    "ingresses": "ingress",
}


_EXPLAIN_RESOURCE_TARGETS: dict[str, tuple[str, str, str]] = {
    "namespaces": ("", "v1", "namespaces"),
    "nodes": ("", "v1", "nodes"),
    "pods": ("", "v1", "pods"),
    "configmaps": ("", "v1", "configmaps"),
    "secrets": ("", "v1", "secrets"),
    "replicationcontrollers": ("", "v1", "replicationcontrollers"),
    "services": ("", "v1", "services"),
    "endpoints": ("", "v1", "endpoints"),
    "events": ("", "v1", "events"),
    "pvc": ("", "v1", "persistentvolumeclaims"),
    "serviceaccounts": ("", "v1", "serviceaccounts"),
    "deployments": ("apps", "v1", "deployments"),
    "replicasets": ("apps", "v1", "replicasets"),
    "daemonsets": ("apps", "v1", "daemonsets"),
    "statefulsets": ("apps", "v1", "statefulsets"),
    "hpa": ("autoscaling", "v2", "horizontalpodautoscalers"),
    "jobs": ("batch", "v1", "jobs"),
    "cronjobs": ("batch", "v1", "cronjobs"),
    "endpointslices": ("discovery.k8s.io", "v1", "endpointslices"),
    "ingress": ("networking.k8s.io", "v1", "ingresses"),
}


_EXPLAIN_GROUP_ALIASES = {
    "deployments.apps": "deployments",
    "replicasets.apps": "replicasets",
    "daemonsets.apps": "daemonsets",
    "statefulsets.apps": "statefulsets",
    "horizontalpodautoscalers.autoscaling": "hpa",
    "jobs.batch": "jobs",
    "cronjobs.batch": "cronjobs",
    "endpointslices.discovery.k8s.io": "endpointslices",
    "ingresses.networking.k8s.io": "ingress",
    "ingress.networking.k8s.io": "ingress",
    "persistentvolumeclaims.v1": "pvc",
    "pods.v1": "pods",
    "services.v1": "services",
}


def parse_command(
    *,
    command: str | None = None,
    argv: list[str] | tuple[str, ...] | None = None,
    default_namespace: str = DEFAULT_NAMESPACE,
) -> ParsedCommand:
    # Boundary validation: these arrive verbatim from client JSON via
    # POST /v1/commands. A list-valued `command` used to reach shlex.split
    # and escape as an AttributeError -> 500; a ValueError here maps to the
    # HTTP layer's 400 handler instead.
    if command is not None and not isinstance(command, str):
        raise ValueError("'command' must be a string")
    if argv is not None and not isinstance(argv, (list, tuple)):
        raise ValueError("'argv' must be a list of strings")
    if argv is None:
        raw = command or ""
        try:
            argv_tuple = tuple(shlex.split(raw))
        except ValueError as exc:
            return ParsedCommand(
                raw_input=raw,
                argv=(),
                family="unknown",
                verb="",
                resource_kind="",
                resource_name="",
                namespace=default_namespace,
                flags={},
                positionals=(),
                parse_error=str(exc),
            )
    else:
        argv_tuple = tuple(str(item) for item in argv)
        raw = command if command is not None else shlex.join(argv_tuple)
    if not argv_tuple:
        return ParsedCommand(raw, argv_tuple, "unknown", "", "", "", default_namespace, {}, ())

    family_token = Path(argv_tuple[0]).name
    family = "kubectl" if family_token in {"kubectl", "k"} else family_token
    namespace, flags, positionals = _split_flags(argv_tuple[1:], default_namespace)
    if family == "kubectl":
        return _parse_kubectl(raw, argv_tuple, namespace, flags, positionals)
    if family == "helm":
        return _parse_helm(raw, argv_tuple, namespace, flags, positionals)
    return ParsedCommand(raw, argv_tuple, family, "", "", "", namespace, flags, tuple(positionals))


def _split_flags(tokens: tuple[str, ...], default_namespace: str) -> tuple[str, dict[str, Any], list[str]]:
    namespace = default_namespace
    flags: dict[str, Any] = {}
    positionals: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in {"-n", "--namespace"}:
            value = tokens[i + 1] if i + 1 < len(tokens) else ""
            namespace = value or namespace
            flags["namespace"] = value
            i += 2
            continue
        if token.startswith("--namespace="):
            value = token.split("=", 1)[1]
            namespace = value or namespace
            flags["namespace"] = value
            i += 1
            continue
        if token in {"-A", "--all-namespaces"}:
            namespace = "*"
            flags[token] = True
            i += 1
            continue
        if token.startswith("--dry-run="):
            flags["--dry-run"] = token.split("=", 1)[1]
            i += 1
            continue
        if token.startswith("-p="):
            flags["-p"] = token.split("=", 1)[1]
            i += 1
            continue
        if token == "-p" and i + 1 < len(tokens) and tokens[i + 1].strip().startswith(("{", "[")):
            flags["-p"] = tokens[i + 1]
            i += 2
            continue
        if token in _VALUE_FLAGS:
            _store_flag_value(flags, token, tokens[i + 1] if i + 1 < len(tokens) else "")
            i += 2
            continue
        if any(token.startswith(prefix + "=") for prefix in _VALUE_FLAGS if prefix.startswith("--")):
            key, value = token.split("=", 1)
            _store_flag_value(flags, key, value)
            i += 1
            continue
        if token.startswith("--") and "=" in token:
            key, value = token.split("=", 1)
            if _is_sensitive_flag_name(key):
                flags[key] = value
                i += 1
                continue
        if token.startswith("-") and _is_sensitive_flag_name(token):
            value = ""
            if i + 1 < len(tokens) and not tokens[i + 1].startswith("-"):
                value = tokens[i + 1]
                i += 2
            else:
                i += 1
            flags[token] = value
            continue
        if token in _BOOL_FLAGS:
            flags[token] = True
            i += 1
            continue
        if token.startswith("-"):
            flags[token] = True
            i += 1
            continue
        positionals.append(token)
        i += 1
    return namespace, flags, positionals


def _store_flag_value(flags: dict[str, Any], key: str, value: str) -> None:
    if key not in _REPEATABLE_VALUE_FLAGS:
        flags[key] = value
        return
    existing = flags.get(key)
    if existing is None:
        flags[key] = value
    elif isinstance(existing, list):
        existing.append(value)
    else:
        flags[key] = [existing, value]


def _flag_values(flags: dict[str, Any], *names: str) -> list[str]:
    values: list[str] = []
    for name in names:
        raw = flags.get(name)
        if isinstance(raw, list):
            values.extend(str(item) for item in raw if str(item))
        elif isinstance(raw, str) and raw:
            values.append(raw)
    return values


def _first_flag_value(flags: dict[str, Any], *names: str, default: str = "") -> str:
    values = _flag_values(flags, *names)
    return values[0] if values else default


def _parse_kubectl(
    raw: str,
    argv: tuple[str, ...],
    namespace: str,
    flags: dict[str, Any],
    positionals: list[str],
) -> ParsedCommand:
    verb = positionals[0] if positionals else ""
    resource_kind = ""
    resource_name = ""
    if verb in {"api-resources", "api-versions", "cluster-info", "version"}:
        return ParsedCommand(raw, argv, "kubectl", verb, "", "", namespace, flags, tuple(positionals))
    if verb == "explain":
        target = positionals[1] if len(positionals) > 1 else ""
        resource_kind, field_path = _split_explain_target(target)
        return ParsedCommand(
            raw, argv, "kubectl", verb,
            resource_kind, field_path, namespace, flags, tuple(positionals)
        )
    if verb == "events":
        return ParsedCommand(raw, argv, "kubectl", "get", "events", "", namespace, flags, tuple(positionals))
    if verb == "logs":
        target = positionals[1] if len(positionals) > 1 else ""
        follow_values = _flag_values(flags, "-f")
        if "-f" in flags:
            flags["--follow"] = True
            if not target and follow_values:
                target = follow_values[0]
        resource_kind, resource_name = _split_resource_token(target, default_kind="pods")
        if not resource_name:
            resource_kind, resource_name = "pods", target
        return ParsedCommand(
            raw, argv, "kubectl", verb,
            resource_kind, resource_name, namespace, flags, tuple(positionals)
        )
    if verb == "auth":
        subverb = positionals[1] if len(positionals) > 1 else ""
        resource_kind = _normalize_kind(positionals[3]) if len(positionals) > 3 else ""
        return ParsedCommand(
            raw, argv, "kubectl", f"auth {subverb}".strip(),
            resource_kind, "", namespace, flags, tuple(positionals)
        )
    if verb == "config":
        subverb = positionals[1] if len(positionals) > 1 else ""
        return ParsedCommand(
            raw, argv, "kubectl", f"config {subverb}".strip(),
            "config", "", namespace, flags, tuple(positionals)
        )
    if verb == "rollout":
        subverb = positionals[1] if len(positionals) > 1 else ""
        target = positionals[2] if len(positionals) > 2 else ""
        resource_kind, resource_name = _split_resource_token(target)
        if not resource_name and len(positionals) > 3:
            resource_name = positionals[3]
        return ParsedCommand(
            raw, argv, "kubectl", f"rollout {subverb}".strip(),
            resource_kind, resource_name, namespace, flags, tuple(positionals)
        )
    if verb in {"delete", "patch", "scale"}:
        target = positionals[1] if len(positionals) > 1 else ""
        resource_kind, resource_name = _split_resource_token(target)
        if not resource_name and len(positionals) > 2:
            resource_name = positionals[2]
        return ParsedCommand(
            raw, argv, "kubectl", verb,
            resource_kind, resource_name, namespace, flags, tuple(positionals)
        )
    if verb == "create":
        resource_kind = _normalize_kind(positionals[1]) if len(positionals) > 1 else ""
        resource_name = positionals[2] if len(positionals) > 2 else ""
        return ParsedCommand(
            raw, argv, "kubectl", verb, resource_kind, resource_name,
            namespace, flags, tuple(positionals)
        )
    if verb in {"apply", "diff"}:
        return ParsedCommand(
            raw, argv, "kubectl", verb, "manifest", "", namespace, flags, tuple(positionals)
        )
    if verb == "wait":
        target = positionals[1] if len(positionals) > 1 else ""
        resource_kind, resource_name = _split_resource_token(target)
        return ParsedCommand(
            raw, argv, "kubectl", "wait",
            resource_kind, resource_name, namespace, flags, tuple(positionals)
        )
    if verb in {"exec", "port-forward"}:
        target = positionals[1] if len(positionals) > 1 else ""
        resource_kind, resource_name = _split_resource_token(target, default_kind="pods")
        if not resource_name:
            resource_kind, resource_name = "pods", target
        return ParsedCommand(
            raw, argv, "kubectl", verb,
            resource_kind, resource_name, namespace, flags, tuple(positionals)
        )
    if len(positionals) > 1:
        resource_kind, resource_name = _split_resource_token(positionals[1])
        if not resource_name and len(positionals) > 2:
            resource_name = positionals[2]
    return ParsedCommand(
        raw, argv, "kubectl", verb, resource_kind, resource_name,
        namespace, flags, tuple(positionals)
    )


def _parse_helm(
    raw: str,
    argv: tuple[str, ...],
    namespace: str,
    flags: dict[str, Any],
    positionals: list[str],
) -> ParsedCommand:
    verb = positionals[0] if positionals else ""
    resource_kind = "release"
    resource_name = ""
    if verb in {"version", "env", "template"}:
        resource_kind = verb
        resource_name = positionals[1] if len(positionals) > 1 else ""
        return ParsedCommand(
            raw, argv, "helm", verb, resource_kind, resource_name,
            namespace, flags, tuple(positionals)
        )
    if verb == "get":
        resource_kind = positionals[1] if len(positionals) > 1 else ""
        resource_name = positionals[2] if len(positionals) > 2 else ""
    elif verb in {"test", "upgrade", "rollback", "uninstall", "install"}:
        resource_kind = verb
        resource_name = positionals[1] if len(positionals) > 1 else ""
    elif len(positionals) > 1:
        resource_name = positionals[1]
    return ParsedCommand(
        raw, argv, "helm", verb, resource_kind, resource_name,
        namespace, flags, tuple(positionals)
    )


def _split_resource_token(token: str, default_kind: str = "") -> tuple[str, str]:
    if not token:
        return default_kind, ""
    if "/" in token:
        raw_kind, name = token.split("/", 1)
    else:
        raw_kind, name = token, ""
    return _normalize_kind(raw_kind or default_kind), name


def _normalize_kind(raw: str) -> str:
    return _KIND_ALIASES.get(raw.lower(), raw.lower())


def _split_explain_target(target: str) -> tuple[str, str]:
    if not target:
        return "", ""
    parts = [part for part in target.split(".") if part]
    for end in range(len(parts), 0, -1):
        raw_resource = ".".join(parts[:end]).lower()
        kind = _normalize_explain_resource(raw_resource)
        if kind in _EXPLAIN_RESOURCE_TARGETS:
            return kind, ".".join(parts[end:])
    return _normalize_explain_resource(parts[0]), ".".join(parts[1:])


def _normalize_explain_resource(raw: str) -> str:
    lowered = raw.lower()
    if lowered in _EXPLAIN_GROUP_ALIASES:
        return _EXPLAIN_GROUP_ALIASES[lowered]
    return _normalize_kind(lowered)


def command_fingerprint(parsed: ParsedCommand, support_status: str) -> str:
    if support_status == "supported":
        return f"{parsed.family} {parsed.verb} {parsed.resource_kind}".strip()
    bits = [
        parsed.family or "unknown",
        parsed.verb or "<missing-verb>",
        parsed.resource_kind or "<missing-kind>",
    ]
    unknown_flags = sorted(
        key for key in parsed.flags
        if key not in {"namespace", "-o", "--output", "-l", "--selector", "-A", "--all-namespaces"}
    )
    if unknown_flags:
        bits.append("flags=" + ",".join(unknown_flags))
    return " ".join(bits)


def guess_intent(parsed: ParsedCommand) -> str:
    if parsed.family == "kubectl":
        if parsed.verb:
            return f"Add kubectl renderer for verb={parsed.verb!r}, kind={parsed.resource_kind or '<none>'!r}."
        return "Add support for the kubectl invocation shape."
    if parsed.family == "helm":
        return f"Add helm renderer for verb={parsed.verb or '<none>'!r}, topic={parsed.resource_kind or '<none>'!r}."
    return "Decide whether this client command belongs in the simulator surface."


def _redact_command_for_trace(parsed: ParsedCommand) -> str:
    if not parsed.argv:
        return parsed.raw_input
    return shlex.join(_redact_argv(parsed.argv))


def _redact_argv(argv: tuple[str, ...]) -> tuple[str, ...]:
    redacted: list[str] = []
    redact_next = False
    for token in argv:
        if redact_next:
            redacted.append("***")
            redact_next = False
            continue
        if _is_sensitive_flag_name(token):
            redacted.append(token)
            redact_next = True
            continue
        if token.startswith("--") and "=" in token:
            key, value = token.split("=", 1)
            if _is_sensitive_flag_name(key):
                redacted.append(f"{key}=***")
                continue
            redacted.append(f"{key}={value}")
            continue
        redacted.append(token)
    return tuple(redacted)


def _redact_parsed_flags(flags: dict[str, Any]) -> dict[str, Any]:
    return {
        key: ("***" if _is_sensitive_flag_name(str(key)) else value)
        for key, value in flags.items()
    }


def _is_sensitive_flag_name(name: str) -> bool:
    lowered = name.lower().lstrip("-")
    return any(token in lowered for token in _SENSITIVE_FLAG_TOKENS)
