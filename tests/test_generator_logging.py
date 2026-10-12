"""Generator diagnostics go through ``logging`` (sd:255).

The generator modules log their warnings under the ``anomaly_metric_creator``
package logger. ``main()`` attaches one handler that prints the bare message
to stderr, so CLI stderr keeps its exact bytes; nothing configures logging at
import.
"""

from __future__ import annotations

import contextlib
import http.client
import io
import logging

from anomaly_metric_creator import run_pipeline

_PACKAGE = "anomaly_metric_creator"


def _package_warnings(caplog) -> list[logging.LogRecord]:
    return [
        r for r in caplog.records
        if r.name.startswith(f"{_PACKAGE}.") and r.levelno == logging.WARNING
    ]


def _cli_handlers() -> list[logging.Handler]:
    return [
        h for h in logging.getLogger(_PACKAGE).handlers
        if getattr(h, "_amc_cli_stderr", False)
    ]


def test_resolve_scenarios_warnings_reach_the_package_logger(amc, tmp_path, caplog):
    """The scenario-drop warnings are log records, in sorted-slug order,
    with their historic text."""
    args = amc.parse_args([
        "--duration-days", "1",
        "--signal-level", "medium",
        "--scenarios", "gateway_ddos,cache_leak_restart",
        "--output-dir", str(tmp_path),
    ])
    with caplog.at_level(logging.WARNING, logger=_PACKAGE):
        amc._resolve_scenarios(args)

    records = _package_warnings(caplog)
    assert [r.name for r in records] == [f"{_PACKAGE}.scenarios_impl"] * 2
    cache, gateway = amc.SCENARIOS["cache_leak_restart"], amc.SCENARIOS["gateway_ddos"]
    assert [r.getMessage() for r in records] == [
        f"WARNING: scenario cache_leak_restart requires --duration-days "
        f">= {cache.days_required} (current: 1.0); skipped.",
        f"WARNING: scenario gateway_ddos requires --signal-level "
        f"{gateway.severity} (current: medium); skipped.",
    ]


def test_import_configures_no_logging():
    """Importing the package adds no handler to the package or root logger
    beyond what ``main()`` attaches."""
    package_logger = logging.getLogger(_PACKAGE)
    assert all(
        getattr(h, "_amc_cli_stderr", False) for h in package_logger.handlers
    ), package_logger.handlers
    assert package_logger.propagate is True
    assert not any(
        type(h).__module__.startswith(_PACKAGE) for h in logging.getLogger().handlers
    )


def test_cli_main_prints_each_warning_once_to_stderr(amc, tmp_path, caplog):
    """Two in-process ``main()`` runs: stderr carries the exact warning line
    once per run (one CLI handler, never stacked), and the record still
    reaches logging."""
    argv = [
        "--duration-days", "1",
        "--interval-seconds", "3600",
        "--signal-level", "medium",
        "--scenarios", "gateway_ddos",
        "--output-dir", str(tmp_path),
    ]
    expected = (
        "WARNING: scenario gateway_ddos requires --signal-level "
        f"{amc.SCENARIOS['gateway_ddos'].severity} (current: medium); skipped.\n"
    )
    for _ in range(2):
        err = io.StringIO()
        caplog.clear()
        with contextlib.redirect_stderr(err), caplog.at_level(logging.WARNING, logger=_PACKAGE):
            amc.main(argv)
        assert err.getvalue().count(expected) == 1, err.getvalue()
        assert [r.getMessage() + "\n" for r in _package_warnings(caplog)] == [expected]
    assert len(_cli_handlers()) == 1


def test_ensure_cli_log_handler_is_idempotent():
    run_pipeline._ensure_cli_log_handler()
    run_pipeline._ensure_cli_log_handler()
    assert len(_cli_handlers()) == 1


def test_cli_handler_writes_to_the_current_stderr():
    """The handler resolves ``sys.stderr`` per record, so a later
    ``redirect_stderr`` receives the line."""
    run_pipeline._ensure_cli_log_handler()
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        logging.getLogger(f"{_PACKAGE}.generation").warning("WARNING: probe 50% done")
    assert err.getvalue() == "WARNING: probe 50% done\n"


def test_otel_retry_and_fail_warnings_reach_the_package_logger(amc, monkeypatch, caplog):
    def _refuse(req, timeout=None):
        raise http.client.BadStatusLine("broken proxy")

    monkeypatch.setattr(
        "anomaly_metric_creator.otel_stream.urllib.request.urlopen", _refuse
    )
    monkeypatch.setattr("anomaly_metric_creator.otel_stream.time.sleep", lambda _s: None)
    with caplog.at_level(logging.WARNING, logger=_PACKAGE):
        sent = amc.stream_otel_signals(
            {"metrics": "http://192.0.2.1/v1/metrics"},
            [{
                "timestamp": "2026-03-10 00:00:00",
                "component": "database",
                "metric": "write_latency_ms",
                "description": "Synthetic failure",
            }],
            speedup=1000000.0,
            timeout_seconds=1.0,
            max_events=1,
            max_retries=1,
        )
    assert sent == 0
    messages = [r.getMessage() for r in _package_warnings(caplog)]
    assert {r.name for r in _package_warnings(caplog)} == {f"{_PACKAGE}.otel_stream"}
    assert len(messages) == 2, messages
    assert messages[0].startswith("WARNING: OTEL metrics stream retry 1/1 for ")
    assert messages[1].startswith("WARNING: OTEL metrics stream failed for ")
