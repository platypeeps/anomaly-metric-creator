"""Focused facade for serve-mode architecture cleanup."""

from __future__ import annotations

from .server_ops import (
    ParsedCommand as ParsedCommand,
    CommandResult as CommandResult,
    parse_command as parse_command,
    run_command as run_command,
    render_command as render_command,
    resource_snapshot as resource_snapshot,
    command_fingerprint as command_fingerprint,
    guess_intent as guess_intent,
    _render_kubectl as _render_kubectl,
    _render_helm as _render_helm,
    _render_get as _render_get,
    _render_describe as _render_describe,
    _render_logs_command as _render_logs_command,
    _table as _table,
    _format_dt as _format_dt,
    _parse_user_timestamp as _parse_user_timestamp,
    _parse_optional_timestamp as _parse_optional_timestamp,
)

__all__ = [
    'ParsedCommand',
    'CommandResult',
    'parse_command',
    'run_command',
    'render_command',
    'resource_snapshot',
    'command_fingerprint',
    'guess_intent',
    '_render_kubectl',
    '_render_helm',
    '_render_get',
    '_render_describe',
    '_render_logs_command',
    '_table',
    '_format_dt',
    '_parse_user_timestamp',
    '_parse_optional_timestamp',
]
