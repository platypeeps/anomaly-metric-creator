"""Atomic artifact publication: temp-sibling write + ``os.replace``.

Covers the ``_atomic_artifact_open`` / ``_atomic_write_text`` helpers, the
stale-``.tmp`` pre-clean sweep, the no-leftovers end-to-end contract, and the
reader-visibility guarantee that motivated the change: a concurrent reader of
``metric_report.log`` (the ``/v1/logs/stream`` shape) can never observe a
truncated or momentarily-missing file while the writer republishes it.
"""

import threading

import pytest

from anomaly_metric_creator import server


def _reporting_rows(n):
    """``write_reporting_artifacts``-shaped rows, big enough that an
    in-place rewrite would expose truncated intermediate states."""
    return [
        {
            "timestamp": f"2026-03-01 00:{i // 60:02d}:{i % 60:02d}",
            "component": "apigateway",
            "metric": "error_rate",
            "description": f"synthetic anomaly row {i} for atomic-write coverage",
        }
        for i in range(n)
    ]


def test_atomic_open_publishes_content_and_removes_tmp(amc, tmp_path):
    target = tmp_path / "artifact.csv"

    with amc._atomic_artifact_open(target) as f:
        f.write("timestamp,m0\n2026-03-01 00:00:00,1.000\n")
        # Not yet published: the final path must not exist mid-write.
        assert not target.exists()
        assert target.with_name("artifact.csv.tmp").exists()

    assert target.read_text(encoding="utf-8") == (
        "timestamp,m0\n2026-03-01 00:00:00,1.000\n"
    )
    assert not target.with_name("artifact.csv.tmp").exists()


def test_atomic_open_replaces_existing_file_whole(amc, tmp_path):
    target = tmp_path / "artifact.csv"
    target.write_text("old content\n", encoding="utf-8")

    with amc._atomic_artifact_open(target) as f:
        f.write("new content\n")
        # Old content stays fully visible until publication.
        assert target.read_text(encoding="utf-8") == "old content\n"

    assert target.read_text(encoding="utf-8") == "new content\n"
    assert not target.with_name("artifact.csv.tmp").exists()


def test_atomic_open_on_error_keeps_target_and_removes_tmp(amc, tmp_path):
    target = tmp_path / "artifact.csv"
    target.write_text("old content\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="mid-write failure"):
        with amc._atomic_artifact_open(target) as f:
            f.write("partial garbage")
            raise RuntimeError("mid-write failure")

    assert target.read_text(encoding="utf-8") == "old content\n"
    assert not target.with_name("artifact.csv.tmp").exists()


def test_atomic_write_text_round_trip(amc, tmp_path):
    target = tmp_path / "schema.json"
    amc._atomic_write_text(target, '{"schema_version": 2}\n')

    assert target.read_text(encoding="utf-8") == '{"schema_version": 2}\n'
    assert not target.with_name("schema.json.tmp").exists()


def test_pre_clean_removes_stale_tmp_files(amc, tmp_path):
    # A crashed prior run can leave temp siblings behind; the next run's
    # pre-clean must sweep them for every registry-known artifact slot,
    # regardless of the emit selection keeping the *final* file alive.
    stale = [
        tmp_path / "apigateway.csv.tmp",
        tmp_path / "anomalies.csv.tmp",
        tmp_path / "metric_report.log.tmp",
        tmp_path / "gauges.csv.tmp",
        tmp_path / (amc._COMBINE_OUTPUT_FILENAME + ".tmp"),
        tmp_path / "schema.json.tmp",
    ]
    for path in stale:
        path.write_text("leftover", encoding="utf-8")
    # A user file unknown to the registry is left alone, tmp-suffixed or not.
    user_note = tmp_path / "notes.txt.tmp"
    user_note.write_text("keep me", encoding="utf-8")
    kept_artifact = tmp_path / "apigateway.csv"
    kept_artifact.write_text("timestamp,m0\n", encoding="utf-8")

    amc._pre_clean_output_dir(
        tmp_path,
        emit_selection={"metrics", "logs", "traces", "gauges", "schema"},
        selected_components=set(amc.COMPONENTS),
        combine=True,
    )

    for path in stale:
        assert not path.exists(), f"stale temp not swept: {path.name}"
    assert user_note.exists()
    assert kept_artifact.exists()


def test_full_run_leaves_no_tmp_files_and_validates(amc, tmp_path, capsys):
    out = tmp_path / "run"
    amc.main([
        "--output-dir", str(out), "--seed", "42", "--duration-days", "1",
        "--emit", "metrics,logs,traces,gauges,schema,combined",
    ])
    capsys.readouterr()

    leftovers = sorted(p.name for p in out.glob("*.tmp"))
    assert leftovers == []
    # The validator's unknown-file check would flag any stray temp too.
    amc.main(["validate", str(out)])
    capsys.readouterr()


def test_concurrent_reader_never_sees_partial_log(amc, tmp_path):
    """Republish metric_report.log repeatedly while a reader polls it.

    Every republication writes identical bytes, so any observed deviation
    (shorter file, missing file) is exactly the truncation/mid-delete window
    the atomic publication is required to close.
    """
    rows = _reporting_rows(2000)
    amc.write_reporting_artifacts(tmp_path, rows, emit_traces=False)
    log_path = tmp_path / "metric_report.log"
    expected = log_path.read_bytes()  # resource-lint: allow
    assert len(expected) > 100_000  # big enough to expose partial writes

    stop = threading.Event()
    failures = []

    def reader():
        while not stop.is_set():
            try:
                observed = log_path.read_bytes()  # resource-lint: allow
            except FileNotFoundError:
                failures.append("file missing during republication")
                return
            if observed != expected:
                failures.append(
                    f"partial read: {len(observed)} bytes "
                    f"!= expected {len(expected)}"
                )
                return

    reader_thread = threading.Thread(target=reader)
    reader_thread.start()
    try:
        for _ in range(30):
            amc.write_reporting_artifacts(tmp_path, rows, emit_traces=False)
    finally:
        stop.set()
        reader_thread.join()

    assert failures == []
    assert log_path.read_bytes() == expected  # resource-lint: allow


def test_continuous_generate_cycles_never_expose_partial_log(amc, tmp_path, capsys):
    """Drive real ``--continuous-generate`` reruns while polling the log.

    ``metric_report.log`` bytes are seed-independent (the manifest rows carry
    no generated values), so every complete previous-or-next state a reader
    may observe across regeneration cycles equals the baseline bytes; any
    other observation is a truncation or mid-delete window.
    """
    argv = [
        "--output-dir", str(tmp_path), "--seed", "77",
        "--duration-days", "1", "--interval-seconds", "3600",
        "--components", "apigateway,cacheservice,database,authservice",
    ]
    amc.main(argv)  # the one-shot startup generation `amc serve` performs
    log_path = tmp_path / "metric_report.log"
    baseline = log_path.read_bytes()  # resource-lint: allow
    assert baseline

    args = amc.parse_args(argv)
    state = server.build_state(amc, args)

    stop = threading.Event()
    failures = []

    def reader():
        while not stop.is_set():
            try:
                observed = log_path.read_bytes()  # resource-lint: allow
            except FileNotFoundError:
                failures.append("file missing during regeneration")
                return
            if observed != baseline:
                failures.append(f"partial read: {len(observed)} bytes")
                return

    reader_thread = threading.Thread(target=reader)
    reader_thread.start()
    try:
        for _ in range(3):
            server._run_continuous_generation_once(state, list(argv))
    finally:
        stop.set()
        reader_thread.join()
    capsys.readouterr()

    assert failures == []
    assert state.generation.last_error == ""
    assert state.generation.generation_count == 3
    # Confirms the seed-independence premise the reader assertion rests on.
    assert log_path.read_bytes() == baseline  # resource-lint: allow
