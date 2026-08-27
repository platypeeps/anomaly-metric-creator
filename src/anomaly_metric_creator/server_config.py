"""``amc serve --config`` loading, validation, and argv conversion.

Extracted from ``server.py`` under the decomposition epic: the config cluster
was the only thing in that module still growing, and it had reached the point
where every addition needed a ratchet bump. The move itself was verbatim; the
module has taken new code since (``_probe_config_server_argv`` among it), so
this is where the cluster lives now rather than a frozen copy of what left. It
is a leaf -- it reads nothing from ``server.py`` -- so the dependency runs one
way and ``server.py`` re-imports every name to keep the historic
``server.<name>`` surface intact.

The generate parser is reached through ``_resolve_generate_parse_args``, which
imports ``legacy`` lazily inside the call. That stays a *call-time* import: at
module scope it would make this leaf depend on the monolith it was extracted
away from.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
from pathlib import Path
from typing import Any, Callable


_SERVE_CONFIG_SERVER_KEYS = {
    "host",
    "port",
    "namespace",
    "debug_ring_size",
    "persist_command_log",
    "persist_command_db",
    "persist_command_retention",
    "persist_mutations",
    "auth_token",
    "max_request_body_bytes",
    "allow_remote_without_auth",
    "cors_allow_origin",
    "rate_limit_per_minute",
    "structured_log",
    "structured_log_file",
    "no_generate",
    "continuous_generate",
    "continuous_generate_interval_seconds",
}


def _load_serve_config(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    is_yaml = suffix in {".yaml", ".yml"}
    if suffix not in {".json", ".yaml", ".yml"}:
        raise _config_error(path, "must be a .json, .yaml, or .yml file")
    if is_yaml:
        try:
            import yaml
        except ImportError as exc:
            raise _config_error(
                path,
                "PyYAML is required to parse YAML files but is not installed. "
                "Install it with 'pip install pyyaml' or use a .json file "
                "instead.",
            ) from exc
        parse_exc_types: tuple[type[Exception], ...] = (
            yaml.YAMLError,
            UnicodeDecodeError,
        )
    else:
        parse_exc_types = (json.JSONDecodeError, UnicodeDecodeError)
    try:
        with path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) if is_yaml else json.load(f)
    except OSError as exc:
        raise _config_error(path, f"failed to read file: {exc}") from exc
    except parse_exc_types as exc:
        label = "YAML" if is_yaml else "JSON"
        raise _config_error(path, f"failed to parse {label}: {exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise _config_error(path, "must contain a JSON/YAML object")
    unknown_top = set(raw) - {"server", "generate"}
    if unknown_top:
        # str() every key before sorting: YAML admits non-string keys, and a
        # mixed set raises TypeError comparing an int to a str -- which would
        # escape the ValueError that names the file, the whole point of
        # validating here.
        raise _config_error(
            path,
            "only accepts top-level 'server' and 'generate' keys; got "
            + ", ".join(sorted(str(key) for key in unknown_top)),
        )
    server = raw.get("server", {})
    generate = raw.get("generate", {})
    if not isinstance(server, dict):
        raise _config_error(path, "server must be an object")
    if not isinstance(generate, dict):
        raise _config_error(path, "generate must be an object")
    # YAML admits non-string keys (`1:`, `true:`), which JSON cannot produce.
    # Left alone they reach `key.replace("_", "-")` and raise AttributeError,
    # escaping the ValueError refusal that names the file -- and they cannot be
    # sorted alongside string keys either. `--config` is an untrusted read-back
    # boundary: check the shape here, on the reader side.
    for section_name, mapping in (("server", server), ("generate", generate)):
        non_string = [key for key in mapping if not isinstance(key, str)]
        if non_string:
            raise _config_error(
                path,
                f"{section_name} keys must be strings; got "
                + ", ".join(repr(key) for key in non_string),
            )
    unknown_server = set(server) - _SERVE_CONFIG_SERVER_KEYS
    if unknown_server:
        # Routed through _config_error like the generate arm: a bad key in
        # either section names the file it came from, which is what the
        # README's `--config` row promises. Attribution is the whole point of
        # validating at load rather than letting a later parse fail bare.
        raise _config_error(
            path,
            "server contains unknown key(s): "
            + ", ".join(sorted(unknown_server)),
        )
    return {"server": dict(server), "generate": dict(generate)}


def _extract_serve_config_path(
    argv: list[str],
    parser: argparse.ArgumentParser,
) -> Path | None:
    config_parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    config_parser.add_argument("--config", type=Path)
    try:
        config_args, _ = config_parser.parse_known_args(argv)
    except SystemExit:
        parser.error("--config requires a file path")
    return config_args.config


def _strip_serve_config_arg(argv: list[str]) -> list[str]:
    """Remove `--config` and its value; everything else passes through.

    Stops at `--`. Argparse treats every token after it as a positional, so
    `_extract_serve_config_path` -- which parses rather than scans -- does not
    see a `--config` there either. Scanning past it would make the two
    disagree: one would strip a token the other never read as a flag.
    """
    result: list[str] = []
    skip_next = False
    stripping = True
    for token in argv:
        if skip_next:
            skip_next = False
            continue
        if stripping and token == "--":
            stripping = False
        elif stripping and token == "--config":
            skip_next = True
            continue
        elif stripping and token.startswith("--config="):
            continue
        result.append(token)
    return result


def _config_error(config_path: Path | None, detail: str) -> ValueError:
    """Build the shared ``--config`` diagnostic so every arm names the file."""
    prefix = f"--config {config_path}: " if config_path is not None else "--config: "
    return ValueError(prefix + detail)


def _config_mapping_to_argv(config: dict[str, Any]) -> list[str]:
    """Convert one config section to argv. Pure conversion, no validation.

    Both sections are validated elsewhere -- `server` names against
    `_SERVE_CONFIG_SERVER_KEYS`, `generate` against the real parser -- so this
    function neither needs the section it is converting nor the file it came
    from.
    """
    argv: list[str] = []
    for key, value in config.items():
        # `null` and `false` are the two shapes that emit nothing, so the argv
        # probe never sees these keys. Conversion stays a pure conversion and
        # _vouch_no_flag_generate_keys checks them separately, against the same
        # real parser -- validating here would need this function to hold the
        # parser too.
        if value is None or value is False:
            continue
        flag = "--" + key.replace("_", "-")
        if isinstance(value, bool):
            # Only `True` reaches here; `False` was skipped above.
            argv.append(flag)
            continue
        # `--flag=value`, never `["--flag", value]`: as a separate token a
        # value starting with `-` is read as an option, so `seed: -1` and
        # `namespace: "-weird"` were rejected as unrecognized flags. The
        # attached form is unambiguous for every value, and argparse accepts it
        # for any optional argument.
        if isinstance(value, (list, tuple)):
            argv.append(f"{flag}={','.join(str(item) for item in value)}")
            continue
        argv.append(f"{flag}={value}")
    return argv


def _resolve_generate_parse_args(legacy_module: Any | None = None) -> Callable[..., Any]:
    """Return the generate parser entrypoint, importing legacy lazily."""
    if legacy_module is None:
        from . import legacy as legacy_module
    return legacy_module.parse_args


_CONFIG_ARGV_VALUE = re.compile(r"(--[A-Za-z0-9][A-Za-z0-9-]*)=\S+")


def _argv_value_literals(argv: list[str]) -> tuple[str, ...]:
    """Every config-supplied value in `argv`, longest first.

    Longest first so a value that contains a shorter one is masked whole
    rather than leaving a fragment behind.
    """
    values = {
        token.split("=", 1)[1] for token in argv if token.startswith("--") and "=" in token
    }
    return tuple(sorted((value for value in values if value), key=len, reverse=True))


def _redact_config_values(diagnostic: str, values: tuple[str, ...] = ()) -> str:
    """Mask config-supplied values a parser echoed back.

    Which keys hold secrets is not knowable here -- a typo'd key is by
    definition on no list -- so every config value is masked and the flag name,
    which is what identifies the mistake, is kept.

    Masking the literal values is what makes this reliable. Argparse echoes a
    value in more forms than the one it was written in: `invalid float value:
    's3cret'` quotes it with no flag attached, and a value containing a space
    is not one whitespace-delimited token. A pattern over the message catches
    neither. The `--flag=value` substitution stays as a second pass, for a
    parser that echoes a token this function was not handed.
    """
    for value in values:
        diagnostic = diagnostic.replace(value, "***")
    return _CONFIG_ARGV_VALUE.sub(r"\1=***", diagnostic)


def _argv_diagnostic(
    stderr: str,
    exc: SystemExit,
    parser_name: str,
    values: tuple[str, ...] = (),
) -> str:
    """Last stderr line, redacted -- or a fallback naming the exit status."""
    lines = [line for line in stderr.strip().splitlines() if line]
    if not lines:
        return f"{parser_name} exited with status {exc.code}"
    return _redact_config_values(lines[-1], values)


def _generate_argv_parses(
    argv: list[str],
    parse_args: Callable[..., Any],
) -> tuple[SystemExit, str] | None:
    """Parse `argv` with both streams captured.

    Returns None if it parsed, otherwise the exit and the parser's stderr --
    the stderr is what lets two failures be compared, so a config is only
    blamed for a failure that is actually its own.
    """
    stderr = io.StringIO()
    try:
        with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(
            io.StringIO()
        ):
            parse_args(list(argv))
    except SystemExit as exc:
        return exc, stderr.getvalue()
    return None


def _refuse_generate_keys_the_serve_parser_owns(
    generate_argv: list[str],
    config_path: Path | None,
    parser: argparse.ArgumentParser,
) -> None:
    """Refuse a `generate` key that is really a serve flag.

    The combined parse lets the serve parser take what it recognizes first, so
    `generate: {port: 9999}` silently sets the server's port -- and the
    generate probe cannot catch it, because by then the token is gone from
    `generate_argv` and the probe sees a section that parses. A `generate` key
    must configure generation, not the server, so a token the serve parser
    consumes is refused here, before the combined parse.

    `--help` is the one flag both parsers own, and `_refuse_exiting_config_argv`
    has already refused it.
    """
    if not generate_argv:
        return
    with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(
        io.StringIO()
    ):
        try:
            _, extra = parser.parse_known_args(list(generate_argv))
        except SystemExit:
            # A parse error here is the generate probe's to report, with its
            # own diagnostic. This arm only cares about what was *consumed*.
            return
    consumed = [token for token in generate_argv if token not in extra]
    if not consumed:
        return
    names = sorted({token.split("=", 1)[0] for token in consumed})
    raise _config_error(
        config_path,
        f"generate key(s) {', '.join(names)} name serve flags, not generate "
        "flags. The serve parser takes them before generation ever sees them, "
        "so they would configure the server from the wrong section. Move them "
        "to the 'server' section.",
    )


def _refuse_exiting_config_argv(
    generate_argv: list[str],
    config_path: Path | None,
    parse_args: Callable[..., Any],
) -> None:
    """Refuse a config whose generate section makes a parser print and exit.

    `help: true` and `version: true` are recognized flags that stop the program
    instead of configuring it, so no config can mean them. This must run before
    the combined parse: the serve parser owns `--help` too and would exit `0`
    on serve usage first, leaving the config unexamined.
    """
    if not generate_argv:
        return
    outcome = _generate_argv_parses(generate_argv, parse_args)
    if outcome is None or outcome[0].code != 0:
        return
    raise _config_error(
        config_path,
        "generate section made the parser print output and exit successfully "
        "instead of producing a configuration -- a key like 'help' or "
        "'version' does this. Remove it.",
    ) from outcome[0]


def _probe_config_generate_argv(
    generate_argv: list[str],
    config_path: Path | None,
    parse_args: Callable[..., Any],
    merged_argv: list[str] | None = None,
) -> None:
    """Reject config-derived generate flags the real parser would not accept.

    The generate surface has no introspectable allowlist -- ``parse_args``
    builds its parser inline -- so rather than hand-maintaining a second list
    that would drift, the real parser *is* the allowlist: parse the
    config-derived argv on its own and convert argparse's exit into a
    ``ValueError`` naming the config file. This is exactly the parse
    ``serve_main`` runs later, moved earlier and given file attribution, so it
    rejects nothing that would have survived anyway.

    Both streams are captured: argparse writes diagnostics to stderr, and a
    ``help: true`` config would otherwise dump usage to stdout.
    """
    if not generate_argv:
        return
    own = _generate_argv_parses(generate_argv, parse_args)
    if own is None:
        return
    own_exit, own_stderr = own
    if own_exit.code == 0:
        # A successful exit is not a rejection: `help: true` makes argparse
        # print usage and stop. Reporting that as "rejected by the parser"
        # names the wrong problem, and the stderr is empty, so the arm below
        # would surface a bare "exited with status 0".
        raise _config_error(
            config_path,
            "generate section made the parser print output and exit "
            "successfully instead of producing a configuration -- a key like "
            "'help' or 'version' does this. Remove it.",
        ) from own_exit
    values = _argv_value_literals(generate_argv)
    own_diagnostic = _argv_diagnostic(own_stderr, own_exit, "generate parser", values)
    if merged_argv is not None:
        merged = _generate_argv_parses(merged_argv, parse_args)
        if merged is None:
            # The config section alone is not what the run will parse. Several
            # generate gates are cross-flag -- the preflight cell cap
            # multiplies interval, duration, metric count, components, and
            # instances -- so `interval_seconds: 1` overflows it against the
            # defaults while the real argv, narrowed by explicit CLI flags, is
            # fine. Refusing on the section alone would reject a working
            # configuration, which the probe is not allowed to do.
            return
        merged_exit, merged_stderr = merged
        merged_diagnostic = _argv_diagnostic(
            merged_stderr, merged_exit, "generate parser", values
        )
        if merged_diagnostic != own_diagnostic:
            # Both fail, but not for the same reason: the run is breaking on
            # something the config did not cause -- a typo in the user's own
            # flags, say. Blaming the config file would send the operator to
            # the wrong place, so this stays quiet and lets the real parse
            # report its own error unattributed. The config is named only for
            # the failure that is verifiably its own.
            return
    raise _config_error(
        config_path,
        "generate section was rejected by the generate parser: " + own_diagnostic,
    ) from own_exit


def _vouch_no_flag_generate_keys(
    config: dict[str, Any],
    config_path: Path | None,
    parse_args: Callable[..., Any],
) -> None:
    """Check the generate keys whose value produces no flag at all.

    ``null`` and ``false`` emit nothing, so the argv probe never sees them and
    a typo would vanish entirely rather than becoming a bogus flag -- the PRD's
    "collides with nothing" case. Refusing both outright would be wrong in the
    other direction: ``otel_verbose: false`` is a real key whose off state is
    exactly what the operator wrote, and refusing it would regress a config
    that works today.

    So each such key is vouched for the same way every other key is -- by
    asking the real parser, never a second hand-maintained list. A key whose
    flag parses *on its own* is a real switch, and dropping it keeps its
    documented meaning of "use the default". Everything else is refused naming
    the file: a typo (``--componentss``), or a value-taking flag where these
    values are meaningless anyway (``--components`` alone is an error, and
    ``components: null`` cannot mean anything else).

    ``server`` keys never come here: they are already name-checked against
    ``_SERVE_CONFIG_SERVER_KEYS``, so neither shape can hide a typo there.
    """
    for key, value in config.items():
        if value is not None and value is not False:
            continue
        flag = "--" + key.replace("_", "-")
        try:
            with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(
                io.StringIO()
            ):
                parse_args([flag])
        except SystemExit as exc:
            shape = "null" if value is None else "false"
            if exc.code == 0:
                # `--help` and `--version` are recognized, so saying the parser
                # does not accept the flag would be false. They exit instead of
                # configuring anything, which no value can make meaningful.
                raise _config_error(
                    config_path,
                    f"generate key '{key}' has a {shape} value, and '{flag}' "
                    "makes the parser print output and exit rather than "
                    "configure a run, so no value for it is meaningful. "
                    "Remove the key.",
                ) from exc
            raise _config_error(
                config_path,
                f"generate key '{key}' has a {shape} value, so it produces no "
                f"flag for the parser to check, and '{flag}' on its own is not "
                "a switch the generate parser accepts. Remove the key to use "
                "its default, or give it a value.",
            ) from exc


def _probe_config_server_argv(
    server_argv: list[str],
    config_path: Path | None,
    parser: argparse.ArgumentParser,
) -> None:
    """Reject config-derived `server` flags the serve parser would not accept.

    The `server` section had its key *names* allowlisted but not its values, so
    `port: "not-a-number"` reached the combined parse and failed as a bare
    ``argument --port: invalid int value`` -- no mention of the file it came
    from, which is the one thing config-time validation exists to provide. This
    is the `generate` probe's counterpart: same parse, run early on the
    config-derived argv alone, with the diagnostic attributed.

    Every serve flag has a default, so a valid section parses on its own.
    """
    if not server_argv:
        return
    stderr = io.StringIO()
    stdout = io.StringIO()
    try:
        with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(stdout):
            _, extra = parser.parse_known_args(list(server_argv))
        if extra:
            # Every argv token here came from an allowlisted key, so the parser
            # consuming none of it means `_SERVE_CONFIG_SERVER_KEYS` has drifted
            # from the parser -- a key nobody can act on. `parse_known_args`
            # would drop it in silence; the real parse below would too, because
            # it must tolerate generate flags in the same argv.
            # Names only, never values: `--auth-token` is an allowlisted server
            # key, so echoing the leftovers verbatim would print a secret into
            # a startup error.
            names = sorted({token.split("=", 1)[0] for token in extra})
            raise _config_error(
                config_path,
                "server section produced flag(s) the serve parser does not "
                f"consume: {', '.join(names)}. _SERVE_CONFIG_SERVER_KEYS has "
                "drifted from the parser.",
            )
    except SystemExit as exc:
        raise _config_error(
            config_path,
            "server section was rejected by the serve parser: "
            + _argv_diagnostic(
                stderr.getvalue(), exc, "serve parser", _argv_value_literals(server_argv)
            ),
        ) from exc


def _parse_serve_args(
    argv: list[str],
    parser: argparse.ArgumentParser,
    *,
    legacy_module: Any | None = None,
) -> tuple[argparse.Namespace, list[str]]:
    raw_argv = list(argv)
    config_path = _extract_serve_config_path(raw_argv, parser)
    config_server_argv: list[str] = []
    config_generate_argv: list[str] = []
    if config_path is not None:
        try:
            config = _load_serve_config(config_path)
            config_server_argv = _config_mapping_to_argv(config["server"])
            config_generate_argv = _config_mapping_to_argv(config["generate"])
            _probe_config_server_argv(config_server_argv, config_path, parser)
            generate_parse_args = _resolve_generate_parse_args(legacy_module)
            _vouch_no_flag_generate_keys(
                config["generate"], config_path, generate_parse_args
            )
            # Runs here, not with the rest of the generate probe below: the
            # combined parse comes first now, and the *serve* parser would
            # consume a config-derived `--help` and exit 0 printing serve
            # usage, so the config would never be judged at all.
            _refuse_exiting_config_argv(
                config_generate_argv, config_path, generate_parse_args
            )
            _refuse_generate_keys_the_serve_parser_owns(
                config_generate_argv, config_path, parser
            )
        except ValueError as exc:
            parser.error(str(exc))
    user_argv = _strip_serve_config_arg(raw_argv)
    serve_args, generate_argv = parser.parse_known_args(
        [*config_server_argv, *config_generate_argv, *user_argv]
    )
    if config_path is not None:
        # Runs after the combined parse, not before it: `generate_argv` is the
        # argv the run will actually hand the generate parser, and the probe
        # needs it to tell a config that is wrong from one that merely looks
        # wrong in isolation.
        try:
            _probe_config_generate_argv(
                config_generate_argv, config_path, generate_parse_args, generate_argv
            )
        except ValueError as exc:
            parser.error(str(exc))
    serve_args.config = config_path
    return serve_args, generate_argv
