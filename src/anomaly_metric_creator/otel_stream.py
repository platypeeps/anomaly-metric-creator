"""OTEL transport streamers and activity-log diagnostics.

Extracted verbatim from ``legacy.py`` (decomposition step 7; see
``.trellis/tasks/07-02-legacy-monolith-decomposition/design.md``).
``legacy.py`` re-imports every moved name so the historic ``legacy.<name>``
surface is unchanged; new code should import from here.
"""

from __future__ import annotations

import base64
import datetime
import heapq
import http.client
import json
import shlex
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Shared retry policy for both OTEL streamers. ``_OTEL_DEFAULT_MAX_RETRIES``
# is the single default threaded into both ``stream_otel_signals`` and
# ``stream_otel_gauges``; ``_OTEL_BACKOFF_MAX_SECONDS`` caps the exponential
# backoff (``min(2 ** (attempts - 1), cap)``).
_OTEL_DEFAULT_MAX_RETRIES = 3
_OTEL_BACKOFF_MAX_SECONDS = 8

from .csv_layout import _iter_component_rows
from .otlp import (
    _build_otlp_gauge_payload,
    _build_otlp_gauge_protobuf,
    _build_otlp_log_payload,
    _build_otlp_log_protobuf,
    _build_otlp_metric_payload,
    _build_otlp_metric_protobuf,
    _build_otlp_trace_payload,
    _build_otlp_trace_protobuf,
)
from .redaction import _masked_headers, _redact_sensitive_headers
from .timeutil import _dt_to_unix_nanos, _parse_csv_timestamp

__all__ = ["stream_otel_gauges", "stream_otel_signals"]


def _write_activity(log_file, event: str, **fields) -> None:
    """Append one activity record. Format: ``ISO_TS EVENT k=v k=v``.

    Values are shell-quoted so embedded whitespace (e.g. ``event_ts`` which uses
    ``YYYY-MM-DD HH:MM:SS``) keeps each ``k=v`` token round-trippable via
    ``shlex.split``.
    """
    if log_file is None:
        return
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    parts = [now, event]
    for k, v in fields.items():
        parts.append(f"{k}={shlex.quote(str(v))}")
    log_file.write(" ".join(parts) + "\n")
    log_file.flush()


def _verbose_body_repr(body: bytes, content_type: str) -> str:
    """Render an OTLP request body for inclusion in the verbose activity log.

    JSON bodies are decoded back to text; protobuf bodies are base64-encoded so
    the log line stays printable and shlex-parseable.
    """
    if "json" in content_type:
        try:
            return body.decode("utf-8")
        except UnicodeDecodeError:
            return base64.b64encode(body).decode("ascii")
    return base64.b64encode(body).decode("ascii")


def _http_error_activity_fields(
    exc, body: bytes, content_type: str, *, verbose: bool = False
) -> dict[str, str]:
    """Return structured activity-log diagnostics for ``HTTPError`` failures.

    Response header values are passed through
    ``_redact_sensitive_headers`` before they are serialized into the
    ``response_headers`` field. That redactor is **mask-unless-known-safe**:
    every value is masked except a short allowlist of non-credential
    operational headers, so an upstream proxy that echoes a credential on a
    4xx/5xx — whether under a standard name (``Set-Cookie``,
    ``Authorization``, ``X-Api-Key``) or a novel one (``X-Amz-Security-Token``,
    ``X-Vault-Token``, ``Authentication-Info``) — never leaks credential
    material into the on-disk log. Known-safe headers (``Content-Type``,
    ``Date``, ``cf-ray``, …) stay legible so the diagnostic remains useful.

    ``response_headers`` and ``cf_ray`` are always-on diagnostics. The
    raw ``request_body`` is included only under ``verbose=True`` —
    matching the ``--otel-verbose`` contract that raw OTLP payload
    bodies reach the activity log only when explicitly requested
    (a failing gauge endpoint would otherwise re-serialize a full
    multi-thousand-data-point batch into the log on every retry).
    """
    if not isinstance(exc, urllib.error.HTTPError):
        return {}

    fields: dict[str, str] = {}
    if exc.headers is not None:
        header_pairs = list(exc.headers.items())
        if header_pairs:
            fields["response_headers"] = json.dumps(
                _redact_sensitive_headers(header_pairs),
                separators=(",", ":"),
            )
        cf_ray = exc.headers.get("cf-ray") if hasattr(exc.headers, "get") else None
        if cf_ray:
            fields["cf_ray"] = cf_ray

    if verbose and "json" in content_type:
        fields["request_body"] = body.decode("utf-8", errors="replace")
    return fields


def _post_with_retries(
    req,
    body: bytes,
    content_type: str,
    *,
    timeout_seconds: float,
    max_retries: int,
    log_file,
    verbose: bool,
    signal: str,
    endpoint: str,
    id_fields: dict,
    verbose_send_fields: dict,
    subject: str,
) -> bool:
    """POST ``req`` with exponential-backoff retries; return success.

    Emits the SEND / OK / RETRY / FAIL activity records both OTEL streamers
    share, so the backoff formula, retry accounting, and record field order
    have one definition. Returns ``True`` when an ``OK`` record was written
    and ``False`` when retries were exhausted (a ``FAIL`` record was
    written). ``id_fields`` is the per-item identity dict every record for
    this item carries (``event_ts``/``component``/``metric`` for signals, or
    ``batch_start_ts``/``batch_end_ts``/``data_points`` for gauges); the
    ``signal, endpoint, *id_fields, attempt, *verbose`` order is preserved
    byte-for-byte from the pre-unification records. ``subject`` is the human
    string for the stderr WARNING lines.

    Catches ``urllib.error.URLError`` (covering ``HTTPError``) **and**
    ``http.client.HTTPException`` — e.g. ``BadStatusLine`` from a malformed
    response, which ``urllib``'s handler does not wrap as ``URLError`` and
    which would otherwise escape and kill the caller. Under ``amc serve``
    that caller is a daemon OTEL thread, so an unhandled transport error
    would silently stop streaming with a bare traceback.
    """
    attempts = 0
    while True:
        _write_activity(
            log_file,
            "SEND",
            signal=signal,
            endpoint=endpoint,
            **id_fields,
            attempt=f"{attempts + 1}/{max_retries + 1}",
            **verbose_send_fields,
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
                response_status = response.status
            ok_fields: dict = {}
            if verbose:
                ok_fields["status"] = response_status
            _write_activity(log_file, "OK", signal=signal, **id_fields, **ok_fields)
            return True
        except (urllib.error.URLError, http.client.HTTPException) as exc:
            attempts += 1
            http_error_fields = _http_error_activity_fields(
                exc, body, content_type, verbose=verbose
            )
            err_fields: dict = {}
            if verbose:
                err_fields["error_type"] = type(exc).__name__
                if isinstance(exc, urllib.error.HTTPError):
                    err_fields["status"] = exc.code
            if attempts > max_retries:
                print(
                    f"WARNING: OTEL {signal} stream failed for {subject}: {exc}",
                    file=sys.stderr,
                )
                _write_activity(
                    log_file,
                    "FAIL",
                    signal=signal,
                    **id_fields,
                    error=repr(str(exc)),
                    **http_error_fields,
                    **err_fields,
                )
                return False
            backoff = min(2 ** (attempts - 1), _OTEL_BACKOFF_MAX_SECONDS)
            print(
                f"WARNING: OTEL {signal} stream retry {attempts}/{max_retries} for "
                f"{subject}: {exc}",
                file=sys.stderr,
            )
            _write_activity(
                log_file,
                "RETRY",
                signal=signal,
                **id_fields,
                attempt=f"{attempts}/{max_retries}",
                error=repr(str(exc)),
                **http_error_fields,
                **err_fields,
            )
            time.sleep(backoff)


def stream_otel_signals(
    endpoints: dict[str, str], # {"logs": url, "metrics": url, "traces": url}
    anomaly_rows: list[dict],
    *,
    speedup: float,
    timeout_seconds: float,
    max_events: int | None = None,
    max_retries: int = _OTEL_DEFAULT_MAX_RETRIES,
    auth_headers: dict[str, dict[str, str]] | None = None, # {"logs": {"Authorization": ...}, ...}
    protocol: str = "json",
    activity_log_path: Path | None = None,
    verbose: bool = False,
) -> int:
    """Replay anomalies to multiple OTLP/HTTP endpoints with timeline-aware pacing.

    Failures are logged to stderr and do not stop generation. When
    ``activity_log_path`` is set, also records one line per send attempt,
    retry, and failure to that file. When ``verbose`` is true, those records
    additionally include the raw request body, request headers (auth tokens
    masked), the HTTP response status on success, and the exception type on
    failure.
    """
    sorted_rows = sorted(anomaly_rows, key=lambda row: row["timestamp"])
    if not sorted_rows:
        return 0

    log_file = None
    prev_dt = None
    sent = 0
    requests_attempted = 0
    aborted = False
    try:
        if activity_log_path is not None:
            activity_log_path.parent.mkdir(parents=True, exist_ok=True)
            log_file = open(activity_log_path, "w", encoding="utf-8")

        active_signals = ",".join(s for s, u in endpoints.items() if u) or "(none)"
        _write_activity(
            log_file,
            "START",
            signals=active_signals,
            events=len(sorted_rows),
            protocol=protocol,
            speedup=speedup,
        )
        for row in sorted_rows:
            if max_events is not None and requests_attempted >= max_events:
                break
            cur_dt = _parse_csv_timestamp(row["timestamp"])
            if prev_dt is not None:
                wait_seconds = max(0.0, (cur_dt - prev_dt).total_seconds() / speedup)
                if wait_seconds > 0:
                    time.sleep(wait_seconds)
            prev_dt = cur_dt

            # Prepare requests for each signal
            for signal, endpoint in endpoints.items():
                if not endpoint:
                    continue
                if max_events is not None and requests_attempted >= max_events:
                    aborted = True
                    break

                if signal == "logs":
                    if protocol == "protobuf":
                        body = _build_otlp_log_protobuf(row)
                        content_type = "application/x-protobuf"
                    else:
                        body = json.dumps(_build_otlp_log_payload(row)).encode("utf-8")
                        content_type = "application/json"
                elif signal == "metrics":
                    if protocol == "protobuf":
                        body = _build_otlp_metric_protobuf(row)
                        content_type = "application/x-protobuf"
                    else:
                        body = json.dumps(_build_otlp_metric_payload(row)).encode("utf-8")
                        content_type = "application/json"
                elif signal == "traces":
                    if protocol == "protobuf":
                        body = _build_otlp_trace_protobuf(row)
                        content_type = "application/x-protobuf"
                    else:
                        body = json.dumps(_build_otlp_trace_payload(row)).encode("utf-8")
                        content_type = "application/json"
                else:
                    continue

                headers = {"Content-Type": content_type}
                if auth_headers and signal in auth_headers:
                    headers.update(auth_headers[signal])

                req = urllib.request.Request(endpoint, data=body, method="POST", headers=headers)
                verbose_send_fields: dict = {}
                if verbose:
                    verbose_send_fields["body"] = _verbose_body_repr(body, content_type)
                    for hk, hv in _masked_headers(headers).items():
                        verbose_send_fields[hk.lower().replace("-", "_")] = hv
                requests_attempted += 1
                if _post_with_retries(
                    req,
                    body,
                    content_type,
                    timeout_seconds=timeout_seconds,
                    max_retries=max_retries,
                    log_file=log_file,
                    verbose=verbose,
                    signal=signal,
                    endpoint=endpoint,
                    id_fields={
                        "event_ts": row["timestamp"],
                        "component": row["component"],
                        "metric": row["metric"],
                    },
                    verbose_send_fields=verbose_send_fields,
                    subject=f"{row['timestamp']} ({row['component']}.{row['metric']})",
                ):
                    sent += 1
            if aborted:
                break
    finally:
        _write_activity(log_file, "END", sent=sent)
        if log_file is not None:
            log_file.close()
    return sent


def stream_otel_gauges(
    component_csv_paths: dict[str, Path],
    *,
    endpoint: str,
    batch_seconds: int,
    metric_prefix: str,
    speedup: float,
    timeout_seconds: float,
    max_events: int | None,
    max_retries: int = _OTEL_DEFAULT_MAX_RETRIES,
    auth_headers: dict[str, str] | None,
    protocol: str,
    activity_log_path: Path | None,
    verbose: bool,
    append_activity_log: bool = True,
) -> int:
    """Stream per-row metric values from per-component CSVs to an OTLP/HTTP
    metrics endpoint as Gauge data points.

    Walks all component CSVs in a unified chronological timeline via
    ``heapq.merge`` keyed on the parsed timestamp, accumulating rows into
    batches that cover ``batch_seconds`` seconds of timeline coverage. Each
    flush is one OTLP request grouped by component (resource) and metric
    (scopeMetrics.metrics). Dropped CSV rows are naturally absent from the
    gauge stream because ``generate_component`` omits them from each per-
    component CSV entirely (see ``keep_mask``), so the streamer only ever
    sees surviving timestamps.

    ``max_events`` caps the total number of OTLP requests sent (not data
    points), mirroring ``--otel-stream-max-events`` semantics for the
    counter stream.

    **CLI-internal surface.** This function is part of a CLI-internal
    surface, not a supported programmatic API: a per-component CSV that
    does not exist on disk is skipped silently rather than raising. That
    is documented semantics, not a defect. See
    ``.trellis/spec/amc/backend/api-cli-server.md`` § Library-API Error
    Posture.
    """
    if not component_csv_paths:
        return 0

    log_file = None

    def _keyed_iter(component: str, csv_path: Path):
        for ts, comp, values, dimensions in _iter_component_rows(component, csv_path):
            yield (_parse_csv_timestamp(ts), ts, comp, values, dimensions)

    # Sort internally (matching write_gauges_csv) so the equal-timestamp
    # component tiebreaker holds regardless of how the caller built the
    # mapping. main() already passes a sorted dict, so the live OTLP
    # emission order is unchanged; this closes the asymmetry for direct
    # callers only.
    iters = [
        _keyed_iter(c, p)
        for c, p in sorted(component_csv_paths.items())
        if p.exists()
    ]

    batch: list[dict] = []
    batch_start_dt: datetime.datetime | None = None
    requests_sent = 0
    requests_attempted = 0
    data_points_sent = 0
    # Pacing key is the previous batch's *start* time so the wall-clock gap
    # between flushes matches the timeline gap between two batch anchors —
    # which is ``batch_seconds`` in steady state. Using the previous batch's
    # *end* time would collapse the gap to roughly ``interval_seconds`` (the
    # spacing between two adjacent CSV rows), producing a 60× pacing error
    # at the default 60s batch.
    prev_batch_start_dt: datetime.datetime | None = None
    aborted = False

    def _flush() -> bool:
        nonlocal batch, batch_start_dt, requests_sent, requests_attempted
        nonlocal data_points_sent, prev_batch_start_dt
        if not batch:
            return True
        # ``max_events`` mirrors the counter stream's semantics: it caps
        # *attempts*, not successes. The counter stream pre-truncates its
        # event list at ``stream_otel_signals`` entry, so the same flag
        # already means "at most N HTTP attempts" there. If we gated on
        # ``requests_sent`` instead, a broken endpoint would let the gauge
        # stream attempt unbounded flushes since none ever succeeds.
        if max_events is not None and requests_attempted >= max_events:
            return False

        if prev_batch_start_dt is not None and batch_start_dt is not None:
            wait_seconds = max(0.0, (batch_start_dt - prev_batch_start_dt).total_seconds() / speedup)
            if wait_seconds > 0:
                time.sleep(wait_seconds)
        requests_attempted += 1

        if protocol == "protobuf":
            body = _build_otlp_gauge_protobuf(batch, metric_prefix=metric_prefix)
            content_type = "application/x-protobuf"
        else:
            body = json.dumps(
                _build_otlp_gauge_payload(batch, metric_prefix=metric_prefix)
            ).encode("utf-8")
            content_type = "application/json"

        headers = {"Content-Type": content_type}
        if auth_headers:
            headers.update(auth_headers)

        req = urllib.request.Request(endpoint, data=body, method="POST", headers=headers)
        batch_start_ts = batch[0]["timestamp"]
        batch_end_ts = batch[-1]["timestamp"]
        data_points = len(batch)
        verbose_send_fields: dict = {}
        if verbose:
            verbose_send_fields["body"] = _verbose_body_repr(body, content_type)
            for hk, hv in _masked_headers(headers).items():
                verbose_send_fields[hk.lower().replace("-", "_")] = hv

        if _post_with_retries(
            req,
            body,
            content_type,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            log_file=log_file,
            verbose=verbose,
            signal="metrics_gauge",
            endpoint=endpoint,
            id_fields={
                "batch_start_ts": batch_start_ts,
                "batch_end_ts": batch_end_ts,
                "data_points": data_points,
            },
            verbose_send_fields=verbose_send_fields,
            subject=f"batch {batch_start_ts}..{batch_end_ts}",
        ):
            requests_sent += 1
            data_points_sent += data_points

        prev_batch_start_dt = batch_start_dt
        batch = []
        batch_start_dt = None
        if max_events is not None and requests_attempted >= max_events:
            return False
        return True

    try:
        if activity_log_path is not None:
            activity_log_path.parent.mkdir(parents=True, exist_ok=True)
            # Append so a prior stream_otel_signals run's records are preserved.
            # Gauge-only CLI mode passes ``append_activity_log=False`` because
            # there is no signal pass creating a fresh log for this run.
            mode = "a" if append_activity_log else "w"
            log_file = open(activity_log_path, mode, encoding="utf-8")

        _write_activity(
            log_file,
            "START",
            signal="metrics_gauge",
            components=",".join(sorted(component_csv_paths.keys())),
            batch_seconds=batch_seconds,
            protocol=protocol,
            speedup=speedup,
        )
        for dt, ts, comp, values, dimensions in heapq.merge(
            *iters, key=lambda item: item[0]
        ):
            if not values:
                continue
            if batch_start_dt is None:
                batch_start_dt = dt
            # Flush when the new row would push the batch beyond batch_seconds
            # of timeline coverage. Use closed-open semantics: a batch_seconds=60
            # batch starting at t=0 covers rows with dt in [0, 60).
            if (dt - batch_start_dt).total_seconds() >= batch_seconds:
                if not _flush():
                    aborted = True
                    break
                batch_start_dt = dt
            # Precompute the nanos once per CSV row, not per data point — the
            # gauge builders read this field directly, skipping the per-point
            # ``strptime`` that previously dominated request-encoding cost.
            ts_nano = _dt_to_unix_nanos(dt)
            for metric_name, value in values:
                batch.append({
                    "timestamp": ts,
                    "time_unix_nano": ts_nano,
                    "component": comp,
                    "metric": metric_name,
                    "value": value,
                    "dimensions": dimensions,
                })
        if not aborted:
            _flush()
    finally:
        _write_activity(
            log_file,
            "END",
            signal="metrics_gauge",
            requests_sent=requests_sent,
            data_points_sent=data_points_sent,
        )
        if log_file is not None:
            log_file.close()
    return requests_sent
