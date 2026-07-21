"""Manifest/CSV coherence under ``--drop-rate`` for shaped span anomalies.

Regression tests for the dropped-first-span-row bug: ``generate_component``
skips overrides at dropped rows, and the ``anomalies.csv`` manifest entry
was historically appended only at ``span_idx == 0``. When the drop mask
happened to hit exactly the first row of a shaped span, the span's
surviving rows still wrote anomalous values into the CSV but no manifest
entry was ever recorded — an anomaly visible in the data with no
manifest row, which also broke the topology-coupling validator's
anomaly-exclusion windows downstream.

The fix records the manifest entry at the spec's first *kept* row. These
tests force the drop mask deterministically (no reliance on a seed
happening to drop the right row) via a crafted RNG whose first
``random(n_rows)`` draw — the drop-mask draw, the first ``random()``
call ``generate_component`` makes — returns a hand-built array.
"""

import numpy as np


class _CraftedDropRng:
    """Delegate to a real ``RandomState`` except for the drop-mask draw.

    ``generate_component``'s first ``rng.random(n_rows)`` call decides the
    drop mask (``< drop_rate`` means dropped). Serve that one call from a
    crafted array — 0.1 for rows to drop, 0.9 for rows to keep, against
    ``drop_rate=0.5`` — and delegate every other attribute to an inner
    seeded ``RandomState`` so natural-value draws stay deterministic.
    """

    def __init__(self, n_rows: int, drop_rows: set[int]):
        self._inner = np.random.RandomState(42)
        self._n_rows = n_rows
        self._drop_rows = set(drop_rows)
        self._drop_draw_served = False

    def random(self, size=None):
        if not self._drop_draw_served and size == self._n_rows:
            self._drop_draw_served = True
            out = np.full(size, 0.9)
            if self._drop_rows:
                out[sorted(self._drop_rows)] = 0.1
            return out
        return self._inner.random(size)

    def __getattr__(self, name):
        return getattr(self._inner, name)


N_ROWS = 20
DROP_RATE = 0.5


def _run_component(amc, tmp_path, drop_rows: set[int], anomaly_specs: list[dict]):
    """Drive ``generate_component`` directly with a deterministic drop mask
    and return the populated ``RunContext`` plus the emitted CSV lines."""
    out = tmp_path / "drop_manifest"
    out.mkdir(exist_ok=True)
    specs = [amc.MetricSpec(name="m0", base=10.0, std=0.0)]
    ctx = amc.RunContext(rng=_CraftedDropRng(N_ROWS, drop_rows))
    ts_array, ts_strings = amc._build_timestamp_arrays(N_ROWS, 1.0)
    amc.generate_component(
        "comp_drop",
        specs,
        anomaly_specs,
        base_dir=out,
        total_seconds=N_ROWS,
        drop_rate=DROP_RATE,
        interval=1.0,
        ts_array=ts_array,
        ts_strings=ts_strings,
        ctx=ctx,
    )
    lines = (out / "comp_drop.csv").read_text().splitlines()  # resource-lint: allow
    return ctx, lines


def _span_spec() -> dict:
    """A 4-row span at rows 5..8 (time_offset=5, duration 4s, interval 1s)."""
    return {
        "time_offset": 5,
        "metric": "m0",
        "description": "span with dropped leading row",
        "generator": lambda ts, col: 99.0,
        "duration_seconds": 4,
    }


def test_span_manifest_recorded_when_first_span_row_dropped(amc, tmp_path):
    """Dropping exactly the span's first row must not lose the manifest
    entry: the entry anchors at the first kept row, and span_end still
    names the last kept row of the span."""
    ctx, lines = _run_component(amc, tmp_path, drop_rows={5}, anomaly_specs=[_span_spec()])

    assert len(ctx.anomalies) == 1, (
        "span with dropped first row lost its manifest entry "
        f"(got {len(ctx.anomalies)} entries) while the CSV still carries "
        "the anomalous values"
    )
    entry = ctx.anomalies[0]
    assert entry["timestamp"] == "2026-03-10 00:00:06"
    assert entry["span_start"] == "2026-03-10 00:00:06"
    assert entry["span_end"] == "2026-03-10 00:00:08"

    # CSV coherence: row 5 absent, surviving span rows carry the override,
    # rows outside the span stay on the natural baseline.
    cells = {line.split(",")[0]: float(line.split(",")[1]) for line in lines[1:]}
    assert "2026-03-10 00:00:05" not in cells
    for second in (6, 7, 8):
        assert cells[f"2026-03-10 00:00:0{second}"] == 99.0
    assert cells["2026-03-10 00:00:04"] == 10.0
    assert cells["2026-03-10 00:00:09"] == 10.0


def test_span_manifest_absent_when_every_span_row_dropped(amc, tmp_path):
    """A span dropped in its entirety emits neither CSV rows nor a
    manifest entry — the single-row-step contract extended to spans."""
    ctx, lines = _run_component(
        amc, tmp_path, drop_rows={5, 6, 7, 8}, anomaly_specs=[_span_spec()]
    )
    assert ctx.anomalies == []
    timestamps = {line.split(",")[0] for line in lines[1:]}
    for second in (5, 6, 7, 8):
        assert f"2026-03-10 00:00:0{second}" not in timestamps


def test_span_manifest_unchanged_when_no_rows_dropped(amc, tmp_path):
    """With no drops the manifest entry is identical to the historic
    ``span_idx == 0`` behavior: anchored at the span's nominal first row."""
    ctx, _ = _run_component(amc, tmp_path, drop_rows=set(), anomaly_specs=[_span_spec()])
    assert len(ctx.anomalies) == 1
    entry = ctx.anomalies[0]
    assert entry["timestamp"] == "2026-03-10 00:00:05"
    assert entry["span_start"] == "2026-03-10 00:00:05"
    assert entry["span_end"] == "2026-03-10 00:00:08"


def test_step_manifest_absent_when_its_row_dropped(amc, tmp_path):
    """Single-row step specs keep the pre-existing contract: a dropped
    row emits neither a CSV row nor a manifest entry."""
    step = {
        "time_offset": 5,
        "metric": "m0",
        "description": "step at dropped row",
        "generator": lambda ts, col: 99.0,
    }
    ctx, lines = _run_component(amc, tmp_path, drop_rows={5}, anomaly_specs=[step])
    assert ctx.anomalies == []
    timestamps = {line.split(",")[0] for line in lines[1:]}
    assert "2026-03-10 00:00:05" not in timestamps
