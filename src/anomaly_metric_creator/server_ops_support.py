"""Pure ops-support helpers shared across the serve-mode surfaces.

Stdlib + ``server_mutations``-only leaf extracted from ``server_ops.py`` (epic
``07-06-server-ops-decomposition`` step 4, Option A). Owns the release/chart
identity constants and the snapshot-row / timestamp / string-coercion /
list-resource-version accessors that both ``server_ops`` and the new
``server_k8s_objects`` / ``server_k8s_tables`` leaves consume downward. It
never imports ``server_ops`` (strict one-way dependency); ``server_ops``
re-imports every public name here at the position ``DEFAULT_RELEASE``
originally held, so the compatibility surface (``server.py``'s alias block,
the k8s facades, ``server_mcp.py``) is unchanged. ``SimulationState`` appears
only in annotations, which ``from __future__ import annotations`` stringizes,
so no runtime import of it is needed.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
from typing import TYPE_CHECKING, Any

from .server_mutations import DEFAULT_NAMESPACE

if TYPE_CHECKING:  # type-checking only; never executed, so the one-way rule holds
    from .server_ops import SimulationState


DEFAULT_RELEASE = "simulated-saas"


DEFAULT_CHART = "simulated-saas-0.3.0"


def _snapshot_row_namespace(row: dict[str, Any], default_namespace: str = DEFAULT_NAMESPACE) -> str:
    return str(row.get("namespace") or default_namespace)


def _snapshot_row_labels(kind: str, row: dict[str, Any]) -> dict[str, str]:
    component = row.get("component") or row.get("owner") or row.get("service") or row.get("name", "")
    labels = {
        "app.kubernetes.io/instance": DEFAULT_RELEASE,
        "app.kubernetes.io/name": str(component),
        "name": str(row.get("name", "")),
    }
    raw_labels = row.get("labels")
    if isinstance(raw_labels, dict):
        labels.update({str(key): str(value) for key, value in raw_labels.items()})
    if kind == "secrets":
        labels.update({"owner": "helm", "name": DEFAULT_RELEASE})
    return labels


def _parse_user_timestamp(value: str) -> _dt.datetime:
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1]
    value = value.replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        with contextlib.suppress(ValueError):
            return _dt.datetime.strptime(value, fmt)
    raise ValueError(f"unsupported timestamp format: {value!r}")


def _parse_optional_timestamp(value: str | None) -> _dt.datetime | None:
    if not value:
        return None
    with contextlib.suppress(ValueError):
        return _parse_user_timestamp(value)
    return None


def _string_dict(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items()}


def _k8s_list_resource_version(state: SimulationState) -> str:
    with state.mutations.lock:
        return str(max(1, state.mutations.version + 1))


def _preview(value: str, limit: int = 240) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."
