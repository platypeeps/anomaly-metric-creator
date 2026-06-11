"""Canonical CLI surface (the consolidation flag-set) and its aliases.

Covers the consolidated surface introduced by the CLI streamline:

- ``--emit`` (with the ``combined`` token) replacing ``--emit-selection``
  + ``--combine``;
- the ``combine`` / ``validate`` subcommands replacing ``--combine-only``
  / ``--validate-output [--validate-warn]``;
- ``--otel-send`` replacing the five OTEL toggles, and ``--otel-endpoint``
  / ``--otel-auth-token`` replacing the per-signal sextet;
- one ``DEPRECATION:`` stderr line per deprecated alias used;
- mixing a canonical flag with the aliases it replaces is rejected.

Byte-equivalence between the canonical and deprecated spellings is pinned
at the cheap 600s interval — the spellings reconcile onto the same
argument namespace before any generation runs, so full-resolution runs
would prove nothing extra.
"""

import contextlib
import io
import subprocess
import sys

import pytest

from conftest import SCRIPT_PATH, run_capture, sha256_path


def _invoke(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        capture_output=True,
        text=True,
    )


def _parse(amc, argv):
    """parse_args with captured stderr; returns (namespace, stderr)."""
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        args = amc.parse_args(argv)
    return args, buf.getvalue()


def _parse_error(amc, argv):
    """Run parse_args expecting SystemExit; returns captured stderr."""
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        with pytest.raises(SystemExit):
            amc.parse_args(argv)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# --emit
# ---------------------------------------------------------------------------


def test_emit_equivalent_to_emit_selection(amc):
    canonical, w = _parse(amc, ["--emit", "metrics,logs", "--output-dir", "x"])
    legacy, _ = _parse(amc, ["--emit-selection", "metrics,logs", "--output-dir", "x"])
    assert canonical.emit_selection == legacy.emit_selection == {"metrics", "logs"}
    assert "DEPRECATION" not in w, "canonical flag must not warn"


def test_emit_combined_token_sets_combine(amc):
    args, _ = _parse(amc, ["--emit", "metrics,combined", "--output-dir", "x"])
    assert args.combine is True
    assert args.emit_selection == {"metrics"}


def test_emit_combined_byte_identical_to_combine_flag(amc, tmp_path):
    """--emit metrics,logs,traces,combined produces byte-identical output
    to the deprecated --combine flag (same default emit selection)."""
    out_new = tmp_path / "new"
    out_old = tmp_path / "old"
    run_capture(amc, out_new, days=1,
                extra_args=["--emit", "metrics,logs,traces,combined"])
    run_capture(amc, out_old, days=1, extra_args=["--combine"])
    for name in ("combined_metrics_unified.csv", "apigateway.csv",
                 "anomalies.csv"):
        assert sha256_path(out_new / name) == sha256_path(out_old / name), name


@pytest.mark.parametrize("bad", [
    ["--emit", "metrics", "--combine"],
    ["--emit", "metrics", "--emit-selection", "logs"],
    ["--emit", "metrics", "--emit-selection=logs"],
])
def test_emit_mixing_with_aliases_rejected(amc, bad):
    err = _parse_error(amc, bad + ["--output-dir", "x"])
    assert "mutually exclusive" in err


def test_emit_combined_alone_rejected(amc):
    err = _parse_error(amc, ["--emit", "combined", "--output-dir", "x"])
    assert "combined" in err


def test_emit_invalid_token_rejected(amc):
    err = _parse_error(amc, ["--emit", "metrics,bogus", "--output-dir", "x"])
    assert "bogus" in err


# ---------------------------------------------------------------------------
# combine / validate subcommands
# ---------------------------------------------------------------------------


def test_combine_subcommand_equivalent_to_combine_only(amc, tmp_path):
    src_dir = tmp_path / "run"
    run_capture(amc, src_dir, days=1, extra_args=["--emit", "metrics"])
    via_sub = tmp_path / "via_sub"
    via_flag = tmp_path / "via_flag"
    import shutil
    shutil.copytree(src_dir, via_sub)
    shutil.copytree(src_dir, via_flag)
    amc.main(["combine", str(via_sub)])
    amc.main(["--combine-only", "--output-dir", str(via_flag)])
    assert sha256_path(via_sub / "combined_metrics_unified.csv") == \
        sha256_path(via_flag / "combined_metrics_unified.csv")


def test_combine_subcommand_rejects_missing_directory(amc):
    with pytest.raises(SystemExit):
        with contextlib.redirect_stderr(io.StringIO()):
            amc.main(["combine", "/nonexistent/run/dir"])


def test_validate_subcommand_clean_run(amc, tmp_path, capsys):
    out = tmp_path / "run"
    run_capture(amc, out, days=1, extra_args=["--emit", "metrics,schema"])
    amc.main(["validate", str(out)])
    assert "OK (no violations)" in capsys.readouterr().out


def test_validate_subcommand_exit_one_on_violation(amc, tmp_path):
    out = tmp_path / "run"
    run_capture(amc, out, days=1, extra_args=["--emit", "metrics,schema"])
    (out / "stray_file.txt").write_text("not declared\n")
    with pytest.raises(SystemExit) as exc_info:
        with contextlib.redirect_stderr(io.StringIO()):
            amc.main(["validate", str(out)])
    assert exc_info.value.code == 1


def test_validate_subcommand_warn_exits_zero(amc, tmp_path):
    out = tmp_path / "run"
    run_capture(amc, out, days=1, extra_args=["--emit", "metrics,schema"])
    (out / "stray_file.txt").write_text("not declared\n")
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        amc.main(["validate", str(out), "--warn"])  # returns, no SystemExit
    assert "violation(s)" in buf.getvalue()


def test_generate_token_equivalent_to_bare_invocation(amc, tmp_path):
    bare = tmp_path / "bare"
    token = tmp_path / "token"
    run_capture(amc, bare, days=1, extra_args=["--emit", "metrics"])
    # ``generate`` is stripped by the dispatcher; drive main() directly so
    # the dispatch path itself is exercised.
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        amc.main(["generate", "--seed", "42", "--duration-days", "1",
                  "--interval-seconds", "60.0", "--emit", "metrics",
                  "--output-dir", str(token)])
    assert sha256_path(bare / "apigateway.csv") == \
        sha256_path(token / "apigateway.csv")


# ---------------------------------------------------------------------------
# --otel-send / --otel-endpoint / --otel-auth-token
# ---------------------------------------------------------------------------


def test_otel_send_gauges_only_mapping(amc):
    args, w = _parse(amc, ["--otel-send", "gauges",
                           "--otel-endpoint", "http://h:4318",
                           "--output-dir", "x"])
    assert args.otel_enabled and args.otel_emit_gauges and args.otel_gauges_only
    assert args.otel_metrics_endpoint == "http://h:4318/v1/metrics"
    # Unselected signals must not stream — even their env-var defaults
    # are overridden by the authoritative --otel-send selection.
    assert args.otel_logs_endpoint is None
    assert args.otel_traces_endpoint is None
    assert "DEPRECATION" not in w


def test_otel_send_signal_subset_derives_only_selected_endpoints(amc):
    args, _ = _parse(amc, ["--otel-send", "logs,traces",
                           "--otel-endpoint", "http://h:4318/",
                           "--otel-auth-token", "tok",
                           "--output-dir", "x"])
    assert args.otel_enabled
    assert not args.otel_emit_gauges and not args.otel_gauges_only
    assert args.otel_logs_endpoint == "http://h:4318/v1/logs"
    assert args.otel_traces_endpoint == "http://h:4318/v1/traces"
    assert args.otel_metrics_endpoint is None
    assert args.otel_logs_auth_token == "tok"
    assert args.otel_traces_auth_token == "tok"


def test_otel_send_all_expands_to_every_signal(amc):
    args, _ = _parse(amc, ["--otel-send", "all",
                           "--otel-endpoint", "http://h:1",
                           "--output-dir", "x"])
    assert args.otel_enabled and args.otel_emit_gauges
    assert not args.otel_gauges_only
    assert args.otel_logs_endpoint and args.otel_metrics_endpoint \
        and args.otel_traces_endpoint


def test_otel_send_none_is_explicit_off(amc, monkeypatch):
    """'none' overrides even an env-var endpoint default — the canonical
    replacement for the deprecated --otel-disabled escape hatch."""
    monkeypatch.setenv("MEZMO_OTEL_LOGS_ENDPOINT", "http://env:1/v1/logs")
    args, _ = _parse(amc, ["--otel-send", "none", "--output-dir", "x"])
    assert not args.otel_enabled


def test_otel_send_equivalent_to_legacy_toggles(amc):
    canonical, _ = _parse(amc, [
        "--otel-send", "logs,metrics,traces,gauges",
        "--otel-endpoint", "http://h:4318", "--output-dir", "x",
    ])
    legacy, _ = _parse(amc, [
        "--otel-enabled", "--otel-emit-gauges",
        "--otel-logs-endpoint", "http://h:4318/v1/logs",
        "--otel-metrics-endpoint", "http://h:4318/v1/metrics",
        "--otel-traces-endpoint", "http://h:4318/v1/traces",
        "--output-dir", "x",
    ])
    for attr in ("otel_enabled", "otel_emit_gauges", "otel_gauges_only",
                 "otel_logs_endpoint", "otel_metrics_endpoint",
                 "otel_traces_endpoint"):
        assert getattr(canonical, attr) == getattr(legacy, attr), attr


@pytest.mark.parametrize("bad,needle", [
    (["--otel-send", "logs", "--otel-enabled"], "mutually exclusive"),
    (["--otel-send", "none", "--otel-gauges-only"], "mutually exclusive"),
    (["--otel-send", "logs"], "--otel-endpoint"),
    (["--otel-endpoint", "http://h:1"], "--otel-send"),
    (["--otel-auth-token", "t"], "--otel-send"),
    (["--otel-send", "none,logs"], "none"),
    (["--otel-send", "bogus", "--otel-endpoint", "http://h:1"], "bogus"),
    (["--otel-send", "logs", "--otel-endpoint", "ftp://h:1"], "http"),
])
def test_otel_canonical_gates(amc, bad, needle):
    err = _parse_error(amc, bad + ["--output-dir", "x"])
    assert needle in err, err


def test_otel_per_signal_flag_overrides_derived_endpoint(amc):
    """An explicit per-signal flag wins over the --otel-endpoint
    derivation for that signal (the documented escape hatch)."""
    args, _ = _parse(amc, [
        "--otel-send", "logs,metrics",
        "--otel-endpoint", "http://base:4318",
        "--otel-logs-endpoint", "http://special:9999/custom/logs",
        "--output-dir", "x",
    ])
    assert args.otel_logs_endpoint == "http://special:9999/custom/logs"
    assert args.otel_metrics_endpoint == "http://base:4318/v1/metrics"


# ---------------------------------------------------------------------------
# Deprecation notices
# ---------------------------------------------------------------------------


def test_each_used_alias_warns_exactly_once(amc):
    _, w = _parse(amc, [
        "--emit-selection", "metrics",
        "--combine",
        "--otel-enabled",
        "--otel-logs-endpoint", "http://h:1/v1/logs",
        "--output-dir", "x",
    ])
    assert w.count("DEPRECATION: --emit-selection ") == 1
    assert w.count("DEPRECATION: --combine ") == 1
    assert w.count("DEPRECATION: --otel-enabled ") == 1
    assert w.count("DEPRECATION: --otel-logs-endpoint ") == 1
    assert w.count("DEPRECATION:") == 4


def test_canonical_surface_never_warns(amc, tmp_path):
    _, w = _parse(amc, [
        "--emit", "metrics,schema,combined",
        "--otel-send", "gauges", "--otel-endpoint", "http://h:1",
        "--otel-auth-token", "t",
        "--output-dir", str(tmp_path),
    ])
    assert "DEPRECATION" not in w


def test_subcommands_do_not_warn(amc, tmp_path, capsys):
    """The combine/validate subcommands reuse alias plumbing internally;
    canonical invocations must not surface DEPRECATION noise."""
    out = tmp_path / "run"
    run_capture(amc, out, days=1, extra_args=["--emit", "metrics,schema"])
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        amc.main(["validate", str(out)])
        amc.main(["combine", str(out)])
    assert "DEPRECATION" not in buf.getvalue()


def test_deprecation_prefix_distinct_from_scenario_warnings(amc):
    """Scenario-drop diagnostics use the 'WARNING: scenario' prefix; the
    alias notices deliberately use 'DEPRECATION:' so stderr filters on
    either prefix cannot cross-match."""
    _, w = _parse(amc, ["--combine", "--output-dir", "x"])
    assert "DEPRECATION:" in w
    assert "WARNING:" not in w


# ---------------------------------------------------------------------------
# Subprocess-level smoke for the new flags (end-to-end argv handling)
# ---------------------------------------------------------------------------


def test_subprocess_emit_and_subcommands_roundtrip(tmp_path):
    out = tmp_path / "run"
    gen = _invoke("--seed", "42", "--duration-days", "1",
                  "--interval-seconds", "600",
                  "--emit", "metrics,schema,combined",
                  "--output-dir", str(out))
    assert gen.returncode == 0, gen.stderr
    assert "DEPRECATION" not in gen.stderr
    assert (out / "combined_metrics_unified.csv").exists()
    val = _invoke("validate", str(out))
    assert val.returncode == 0, val.stderr
    assert "OK (no violations)" in val.stdout


def test_subcommand_directory_errors_distinguish_missing_from_file(amc, tmp_path):
    """A path that exists but is a file gets a 'not a directory' error,
    not a misleading 'does not exist' (Copilot review on PR #101)."""
    not_a_dir = tmp_path / "file.txt"
    not_a_dir.write_text("x\n")
    for sub in ("combine", "validate"):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            with pytest.raises(SystemExit):
                amc.main([sub, str(not_a_dir)])
        assert "exists but is not one" in buf.getvalue(), (sub, buf.getvalue())
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            with pytest.raises(SystemExit):
                amc.main([sub, str(tmp_path / "missing")])
        assert "does not exist" in buf.getvalue(), (sub, buf.getvalue())
