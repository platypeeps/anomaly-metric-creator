"""A mistyped flag is refused by name, and no command-line value is echoed.

argparse's own ``unrecognized arguments: <tokens>`` message quoted raw argv, so
``amc serve --auth-tokn s3cret`` printed the token to stderr, and a serve-level
typo was refused by the generate parser, in its voice. These tests pin both
halves: the value never appears, and serve reports serve typos itself.
"""

from __future__ import annotations

import argparse
import json

import pytest

from anomaly_metric_creator import cli, legacy, server, server_config
from anomaly_metric_creator.cli_argv_safety import (
    UnrecognizedArguments,
    ValueSafeArgumentParser,
    describe_unrecognized,
    flag_names,
)

SECRET = "s3cret-must-not-appear"


# -- the helper -------------------------------------------------------------


@pytest.mark.parametrize(
    ("tokens", "expected"),
    [
        (["--auth-tokn", SECRET], ["--auth-tokn"]),
        ([f"--auth-tokn={SECRET}"], ["--auth-tokn"]),
        # A negative number is a value, by argparse's own prefix rule.
        (["--por", "-5"], ["--por"]),
        (["--ratio", "-.5"], ["--ratio"]),
        (["-x", SECRET], ["-x"]),
        # Nothing but dashes names no flag, including after an `=` cut.
        (["--", "-", f"--={SECRET}"], []),
        ([SECRET], []),
        (["--b=0", "--a=1", "--b=2"], ["--a", "--b"]),
        # A dash-led value after a flag with no `=` is that flag's value.
        (["--auth-tokn", "-s3cret"], ["--auth-tokn"]),
        (["--a", "--b", "-c"], ["--a"]),
        (["--a=1", "--b"], ["--a", "--b"]),
        (["--a", "val", "--b"], ["--a", "--b"]),
        # Everything after a bare `--` is positional, however it is spelled.
        (["--", "--s3cret"], []),
        (["--typo", "--", "--s3cret"], ["--typo"]),
    ],
)
def test_flag_names_keeps_flags_and_drops_every_value(tokens, expected):
    assert flag_names(tokens) == expected


def test_describe_unrecognized_never_contains_a_value():
    message = describe_unrecognized(["--auth-tokn", SECRET, f"--x={SECRET}"])
    assert "--auth-tokn" in message and "--x" in message
    assert SECRET not in message


def test_describe_unrecognized_with_no_flag_says_so_without_the_token():
    message = describe_unrecognized([SECRET])
    assert SECRET not in message
    assert "none of them is a flag" in message


# -- the parser class -------------------------------------------------------


def _parser(**kwargs) -> ValueSafeArgumentParser:
    parser = ValueSafeArgumentParser(prog="probe", **kwargs)
    parser.add_argument("--count", type=int, default=0)
    return parser


def test_unrecognized_arguments_is_a_usage_exit_naming_only_flags(capsys):
    with pytest.raises(UnrecognizedArguments) as excinfo:
        _parser().parse_args(["--auth-tokn", SECRET])
    # Still a SystemExit with argparse's usage code, so no caller changes.
    assert isinstance(excinfo.value, SystemExit)
    assert excinfo.value.code == 2
    assert excinfo.value.flags == ("--auth-tokn",)
    err = capsys.readouterr().err
    assert "probe: error: unrecognized arguments: --auth-tokn" in err
    assert SECRET not in err


def test_exit_on_error_false_raises_argument_error_without_the_value():
    with pytest.raises(argparse.ArgumentError) as excinfo:
        _parser(exit_on_error=False).parse_args([f"--auth-tokn={SECRET}"])
    assert "--auth-tokn" in str(excinfo.value)
    assert SECRET not in str(excinfo.value)


def test_other_errors_keep_argparse_own_message(capsys):
    # Out of scope by design: a bad value for a flag the parser owns is still
    # argparse's message, and it is not intercepted.
    with pytest.raises(SystemExit) as excinfo:
        _parser().parse_args(["--count", "abc"])
    assert not isinstance(excinfo.value, UnrecognizedArguments)
    assert "invalid int value" in capsys.readouterr().err


# -- the generate CLI -------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["--typo-flag", SECRET],
        [f"--typo-flag={SECRET}"],
        ["--typo-flag", f"-{SECRET}"],
        ["--typo-flag", "--", f"--{SECRET}"],
    ],
)
def test_generate_parser_refuses_a_typo_without_its_value(argv, capsys):
    with pytest.raises(UnrecognizedArguments) as excinfo:
        legacy.parse_args(argv)
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "unrecognized arguments: --typo-flag" in err
    assert SECRET not in err


# -- amc serve --------------------------------------------------------------


def _serve_stderr(argv, capsys) -> str:
    """Run the real `amc serve` dispatch and return its stderr.

    Every argv here fails to parse, so nothing binds a socket.
    """
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["serve", *argv])
    assert excinfo.value.code == 2
    return capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        ["--auth-tokn", SECRET],
        [f"--auth-tokn={SECRET}"],
        ["--auth-tokn", f"-{SECRET}"],
        ["--auth-tokn", "--", f"--{SECRET}"],
    ],
    ids=["separate", "joined", "dash-led", "after-double-dash"],
)
def test_serve_refuses_a_mistyped_auth_flag_without_echoing_the_secret(argv, capsys):
    err = _serve_stderr(argv, capsys)
    assert SECRET not in err
    assert "--auth-tokn" in err
    # Refused in serve's voice: serve's prog and serve's own guidance, not the
    # generate parser's usage dump.
    assert "serve: error: unrecognized arguments: --auth-tokn" in err
    assert "amc serve --help" in err


def test_serve_refuses_a_mistyped_config_flag_without_its_path(tmp_path, capsys):
    config_path = tmp_path / "secret-name-serve-config.json"
    config_path.write_text(
        json.dumps({"server": {"auth_token": SECRET}, "generate": {}}),
        encoding="utf-8",
    )
    err = _serve_stderr(["--conf", str(config_path)], capsys)
    assert "unrecognized arguments: --conf" in err
    assert "secret-name-serve-config" not in err
    assert SECRET not in err


def test_serve_refuses_a_mistyped_port_flag_without_its_value(capsys):
    err = _serve_stderr(["--por", "49157"], capsys)
    assert "unrecognized arguments: --por" in err
    assert "49157" not in err


def test_serve_refuses_a_stray_positional_without_echoing_it(capsys):
    parser = server._build_serve_parser()
    with pytest.raises(SystemExit):
        server_config._parse_serve_args([SECRET], parser)
    err = capsys.readouterr().err
    assert SECRET not in err
    assert "none of them is a flag" in err


def _a_forwarded_generate_flag() -> str:
    """One `--flag=value` the generate parser owns and the serve parser does not.

    Derived from the real parsers, not hard-coded: the candidates are the
    generate defaults' own dests, each probed through both parsers.
    """
    serve_parser = server._build_serve_parser()
    for dest, default in sorted(vars(legacy.parse_args([])).items()):
        if isinstance(default, bool) or not isinstance(default, int):
            continue
        token = f"--{dest.replace('_', '-')}={default}"
        _, leftover = serve_parser.parse_known_args([token])
        if leftover != [token]:
            continue  # the serve parser owns this one
        try:
            legacy.parse_args([token])
        except SystemExit:
            continue
        return token
    raise AssertionError("no generate-only int flag found to forward")


def test_a_generate_flag_serve_forwards_still_reaches_generation():
    token = _a_forwarded_generate_flag()
    parser = server._build_serve_parser()
    _serve_args, generate_argv = server_config._parse_serve_args([token], parser)
    assert token in generate_argv


def test_a_config_generate_key_no_parser_owns_is_still_blamed_on_the_file(
    tmp_path, capsys
):
    # The new check runs after the config probe, so a bad key the *file*
    # carries keeps its file attribution rather than becoming a generic typo.
    config_path = tmp_path / "serve-config.json"
    config_path.write_text(
        json.dumps({"server": {}, "generate": {"no_such_generate_key": SECRET}}),
        encoding="utf-8",
    )
    parser = server._build_serve_parser()
    with pytest.raises(SystemExit):
        server_config._parse_serve_args(["--config", str(config_path)], parser)
    err = capsys.readouterr().err
    assert str(config_path) in err
    assert "--no-such-generate-key" in err
    assert SECRET not in err
