"""Command-render primitives and the ``CommandResult`` return type.

Pure leaf below :mod:`server_ops`: it imports only stdlib, ``ParsedCommand``
from :mod:`server_ops_parse`, and re-exports ``_format_dt`` from
:mod:`server_mutations` (the byte-identical canonical copy). It holds the
``CommandResult`` dataclass every command renderer returns and the general
render/command helpers (``_table``, ``_is_dry_run``, ``_unsupported``,
``_exposed_active_scenarios``) shared by the command renderers and the future
``server_helm_impl`` leaf.

The module never imports :mod:`server_ops` (one-way rule). ``SimulationState``
is referenced only in an annotation, resolved through the ``TYPE_CHECKING``
import below; with ``from __future__ import annotations`` the annotation is a
string and is never evaluated at runtime, so no reverse runtime import exists.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .server_mutations import _format_dt as _format_dt  # re-export; byte-identical
from .server_ops_parse import ParsedCommand

if TYPE_CHECKING:
    from .server_ops import SimulationState

__all__ = [
    "CommandResult",
    "_table",
    "_is_dry_run",
    "_unsupported",
    "_exposed_active_scenarios",
    "_format_dt",
]


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str
    support_status: str
    matched_rule_id: str


def _is_dry_run(parsed: ParsedCommand) -> bool:
    raw = parsed.flags.get("--dry-run")
    if raw is None:
        return False
    if raw is True:
        return True
    return str(raw).strip().lower() not in {"", "false", "none", "0"}


def _unsupported(parsed: ParsedCommand, label: str) -> CommandResult:
    return CommandResult(
        1,
        "",
        f"{label} is not implemented by the simulator yet\n",
        "unsupported",
        "unsupported",
    )


def _exposed_active_scenarios(state: SimulationState) -> tuple[str, ...]:
    """Active scenario slugs for investigation-open ops surfaces.

    Empty in eval mode (`amc serve --mcp-eval-mode`). The active scenarios
    are the eval harness's scoring rubric, so no ops surface reachable by
    the agent under evaluation — ConfigMap data, ``kubectl exec ... env``,
    ``helm get values``, the Helm release payload, or pod ``scenario_ids``
    — may name them. The value collapses to empty rather than a marker
    string so eval output is indistinguishable from a legitimate
    zero-scenario run (fingerprint-resistant). Behavioral signals
    (unhealthy pods, events, ``ScenarioInfluenced`` status) are
    deliberately unaffected: the agent must still observe the *symptoms*,
    just not the labels.
    """
    return () if state.eval_mode else state.active_scenarios


def _table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(header) for header in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    lines = ["  ".join(header.ljust(widths[i]) for i, header in enumerate(headers))]
    for row in rows:
        lines.append("  ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row)))
    return "\n".join(lines) + "\n"
