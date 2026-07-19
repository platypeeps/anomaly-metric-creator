"""Static topology metric registries and tuning constants."""

from __future__ import annotations

# Phase 3: per-component "load metrics" the topology coupling
# operates on. Each entry maps a component to a
# ``(canonical, supplementary)`` tuple where:
#
# * ``canonical`` is the single MetricSpec.name a constant-weight edge
#   from this component reads to produce its contribution. Required;
#   must be a captured MetricSpec on the component.
# * ``supplementary`` is the (possibly empty) tuple of additional
#   MetricSpec.name values captured alongside the canonical metric so
#   ``Edge.signal`` callables on outgoing edges can derive a per-row
#   scalar from multiple columns (e.g. cacheservice exposes both
#   ``cache_hits`` and ``cache_misses`` so the cache→database miss-ratio
#   signal can compute ``misses / (hits + misses)``).
#
# Components with a single load metric have ``supplementary = ()``.
# Constant-weight edges always read ``canonical``; the capture loop
# captures ``(canonical, *supplementary)`` into ``topology_capture``;
# ``_compose_topology_coupled_specs`` rewrites both canonical and
# supplementary metrics on downstream components that have incoming
# edges. Declared above ``TOPOLOGY`` so ``_validate_topology()`` (which
# runs at import time) can build a captured-column probe for callable-
# weight edges' ``signal`` callables.
_TOPOLOGY_LOAD_METRICS: dict[str, tuple[str, tuple[str, ...]]] = {
    "loadbalancer": ("requests_per_sec", ()),
    "apigateway": ("requests_per_sec", ()),
    "authservice": ("login_attempts", ()),
    "cacheservice": ("cache_hits", ("cache_misses",)),
    "database": ("queries_per_sec", ()),
    # phase 5: llm_analytics couples its token throughput to
    # apigateway under realistic mode. ``input_tokens_per_sec`` is the
    # canonical "load" metric here because the token budget governs
    # tokens/second (not requests/second) — pinning the load metric to
    # tokens also gives the coupling enough signal-to-noise to clear
    # the >= 0.85 Pearson correlation gate, given the noise floor at
    # ``_TOPOLOGY_COUPLE_NOISE_STD`` is fixed in absolute units.
    # No downstream consumes llm_analytics in the v1 graph, so there
    # are no supplementary columns.
    "llm_analytics": ("input_tokens_per_sec", ()),
}


# Phase 2/3: standard deviation of the additive noise
# injected on top of the coupled upstream signal under realistic
# topology coupling. Kept small (5.0) relative to the typical
# coupling signal std (~15–1600 depending on component) so the Pearson
# correlation between upstream and downstream stays well above every
# gate that reads it — the 0.95 phase-2 acceptance threshold in
# ``tests/test_topology_loadbalancer_gateway.py``, the 0.9 phase-3
# thresholds in ``tests/test_topology_fanout.py``, and the validator's
# ``_TOPOLOGY_DEFAULT_CORRELATION_THRESHOLD = 0.85`` — while the
# column still looks like a noisy signal rather than a perfect copy
# of the upstream.
_TOPOLOGY_COUPLE_NOISE_STD = 5.0


# Per-component map of ``(latency_metrics, error_metrics)`` that
# incoming saturating TOPOLOGY edges modulate. The latency metrics get
# the per-edge ``latency_multiplier`` composed multiplicatively into
# their ``MetricSpec.multiplier``; the error metrics get the per-edge
# ``error_offset`` added to their ``MetricSpec.additive``. Components
# absent from this map are saturation-inert even when they have
# incoming saturating edges, so additional downstream targets can be
# added here without touching the front-half wiring. Phase 4
# wired the four front-half targets (apigateway and its three fan-out
# downstreams); phase 5 added ``llm_analytics`` for the
# token-throttle response.
_TOPOLOGY_SATURATION_TARGETS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "apigateway": (
        ("avg_response_time_ms", "backend_latency_ms"),
        ("error_rate",),
    ),
    "authservice": (
        ("avg_auth_latency_ms",),
        ("error_rate",),
    ),
    "cacheservice": (
        ("avg_cache_latency_ms",),
        ("error_rate",),
    ),
    "database": (
        ("read_latency_ms", "write_latency_ms"),
        ("error_rate",),
    ),
    # phase 5: under apigateway saturation (the LLM token
    # budget), the llm_analytics latency family lifts via the logistic
    # multiplier and the LLM-specific error rate lifts via the additive
    # offset. The catalog exposes ``llm_api_error_rate`` (not the
    # generic ``error_rate``) so the LLM error column is the right
    # additive target.
    "llm_analytics": (
        ("avg_llm_latency_ms", "p95_llm_latency_ms"),
        ("llm_api_error_rate",),
    ),
}
