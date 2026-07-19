"""Anomaly shape and generator-call dispatch helpers.

Extracted from ``legacy.py`` as part of the generation/topology decomposition.
``legacy.py`` re-imports these names so the historic test and package surface
continues to resolve through ``anomaly_metric_creator.legacy``.
"""

from __future__ import annotations

import datetime
import inspect
from typing import Callable

try:
    import numpy as np
except ModuleNotFoundError as exc:
    if exc.name not in {None, "numpy"}:
        raise
    raise SystemExit(
        "Missing required dependency: numpy\n"
        "Install this project into the Python you are using, for example:\n"
        "  python3 -m pip install -e .\n"
        "or create the documented dev environment:\n"
        "  python3 -m venv .venv\n"
        "  .venv/bin/pip install -e '.[dev]'\n"
    ) from None

# Anomaly shape vocabulary recognised by ``_resolve_anomaly_value``. Specs that
# declare an unknown ``shape`` are rejected at import time by
# ``_validate_scenario_spec``.
_VALID_ANOMALY_SHAPES = frozenset({
    "step", "sustained", "ramp_linear", "ramp_exp", "sawtooth", "sine",
})


def _resolve_anomaly_value(spec: dict, ts: datetime.datetime, col: int,
                           t_within: float, span_idx: int,
                           rng: "np.random.RandomState" = None) -> float:
    """Resolve one anomaly value at one row, honoring shape/duration fields."""
    duration_seconds = float(spec.get("duration_seconds", 0) or 0)
    shape = spec.get("shape", "step")
    shape_params = spec.get("shape_params", {}) or {}

    if duration_seconds <= 0 and shape == "step":
        # Dispatch by REQUIRED positional count, not by maximum callability.
        # A generator like (ts, col, scale=1.0) accepts a 3-arg call at the
        # Python language level, but the author marked the 3rd positional
        # as optional with a non-rng name — calling 3-arg would silently
        # bind the RNG object to ``scale``. Required-based dispatch keeps
        # the default and avoids the misbind. Only generators that
        # explicitly opt into the RNG (required=3 or *args) receive it.
        meta = _cached_generator_meta(spec["generator"])
        if not meta["inspectable"]:
            # Conservative fallback: try only the two canonical shapes
            # (3-arg first, then 2-arg). No intermediate calls. Retry
            # only on a call-*binding* TypeError (arity mismatch raised
            # at the call site: the traceback has no frame beyond this
            # one). A TypeError raised *inside* the generator body has a
            # deeper traceback; retrying it with 2 args would mask the
            # real bug and — if the body drew from ``rng`` before
            # raising — double-advance the RNG stream. (A C-extension
            # body raising TypeError without Python frames is
            # indistinguishable from a binding failure and still
            # retries; that is the best the fallback can do.)
            try:
                value = spec["generator"](ts, col, rng)
            except TypeError as exc:
                if exc.__traceback__.tb_next is not None:
                    raise
                value = spec["generator"](ts, col)
            return float(value)
        required = meta["required_positional"]
        fixed = meta["fixed_positional_count"]
        if meta["has_var_positional"]:
            # Mirror the validator's *args misbind check so direct callers
            # (e.g., tests bypassing _validate_scenario_spec) cannot silently
            # bind the RNG to a default-having fixed positional like
            # ``scale`` in ``(ts, col, scale=1.0, *args)``.
            if required <= 2 and fixed > 2:
                # Step path calls 3-arg, so the only position the dispatcher
                # could misbind onto is fixed position 3. Positions 4+ are
                # left at their declared defaults (not bound by the 3-arg
                # call), so name the actual offender — position 3 — rather
                # than the count of fixed params.
                raise TypeError(
                    f"Generator {spec['generator']!r} has *args with "
                    f"fixed_positional_count={fixed} > 2 and required <= 2; "
                    f"the 3-arg step call would overwrite the default-having "
                    f"fixed positional at position 3. Use (ts, col) or "
                    f"(ts, col, rng) instead."
                )
            return float(spec["generator"](ts, col, rng))
        if required == 3:
            return float(spec["generator"](ts, col, rng))
        if required <= 2:
            return float(spec["generator"](ts, col))
        raise TypeError(
            f"Generator {spec['generator']!r} requires {required} positional "
            f"args; step-path specs must use a 2-arg or 3-arg required shape."
        )

    if shape in ("step", "sustained"):
        return float(_call_generator_within_span(spec["generator"], ts, col, t_within, span_idx, rng))

    start = shape_params.get("start")
    if start is None:
        start = _call_generator_within_span(spec["generator"], ts, col, 0.0, 0, rng)
    start = float(start)

    if shape == "ramp_linear":
        end = float(shape_params.get("end", start))
        frac = _span_fraction(t_within, duration_seconds)
        return start + (end - start) * frac

    if shape == "ramp_exp":
        end = float(shape_params.get("end", start))
        exponent = float(shape_params.get("exponent", 3.0))
        frac = _span_fraction(t_within, duration_seconds) ** exponent
        return start + (end - start) * frac

    if shape == "sawtooth":
        period = float(shape_params.get("period_s", max(duration_seconds, 1.0)))
        amplitude = float(shape_params.get("amplitude", 0.0))
        midline = float(shape_params.get("midline", start))
        phase = float(shape_params.get("phase_s", 0.0))
        cycle = ((t_within + phase) / max(period, 1e-9)) % 1.0
        return midline - amplitude + (2.0 * amplitude * cycle)

    if shape == "sine":
        period = float(shape_params.get("period_s", max(duration_seconds, 1.0)))
        amplitude = float(shape_params.get("amplitude", 0.0))
        midline = float(shape_params.get("midline", start))
        phase = float(shape_params.get("phase_s", 0.0))
        angle = 2.0 * np.pi * ((t_within + phase) / max(period, 1e-9))
        return midline + amplitude * np.sin(angle)

    raise ValueError(f"Unsupported anomaly shape: {shape}")


class _IdentityKey:
    """Dict key with identity-based equality, used by the generator-meta
    cache. Two distinct callables that compare equal via custom ``__eq__``
    must not share cached metadata; keying by identity avoids that.
    Storing the object inside the key also keeps a strong reference,
    so Python can't recycle ``id(obj)`` for a different generator after
    garbage collection."""
    __slots__ = ("obj",)

    def __init__(self, obj):
        self.obj = obj

    def __hash__(self):
        return id(self.obj)

    def __eq__(self, other):
        return isinstance(other, _IdentityKey) and self.obj is other.obj


_GENERATOR_META_CACHE: "dict[_IdentityKey, dict]" = {}
_GENERATOR_META_CACHE_MAX = 1024


def _generator_meta(gen) -> dict:
    """Return introspection metadata for a generator callable.

    Tracking *required* and *maximum* positional separately matters because
    a generator like ``(ts, col, rng=None, extra=None)`` has 2 required +
    2 optional positional params (4 max), so the runtime can call it with
    2, 3, or 4 positional args. The validator and dispatcher both consult
    this metadata to pick a safe call shape.

    Keys returned:
    - ``required_positional``: count of positional-only or
      positional-or-keyword params with no default. The minimum positional
      arity the callable accepts.
    - ``fixed_positional_count``: count of positional-only or
      positional-or-keyword params total (with or without defaults).
      Preserved even when ``*args`` is present, because a fixed-positional
      prefix BEFORE ``*args`` still receives the first N positional args
      of a call before the rest flow into ``*args``.
    - ``max_positional``: total positional capacity. Equals
      ``fixed_positional_count`` when ``*args`` is absent; ``None`` when
      ``*args`` is present (unbounded).
    - ``has_var_positional``: True iff ``*args`` is in the signature. The
      validator and both dispatchers consult this flag to decide whether
      to call the canonical target-arity shape.
    - ``has_required_kwargs``: True iff any ``KEYWORD_ONLY`` param has no
      default. Such generators cannot be called positionally by our runtime.
    - ``inspectable``: True iff ``inspect.signature()`` succeeded. When
      False, callers must fall back to a try/except call chain.
    """
    try:
        sig = inspect.signature(gen)
    except (TypeError, ValueError):
        return {"required_positional": 0,
                "fixed_positional_count": 0,
                "max_positional": None,
                "has_var_positional": False,
                "has_required_kwargs": False,
                "inspectable": False}
    required = 0
    fixed = 0
    has_var_positional = False
    has_required_kw = False
    for p in sig.parameters.values():
        if p.kind is inspect.Parameter.VAR_POSITIONAL:
            has_var_positional = True
        elif p.kind in (inspect.Parameter.POSITIONAL_ONLY,
                        inspect.Parameter.POSITIONAL_OR_KEYWORD):
            fixed += 1
            if p.default is inspect.Parameter.empty:
                required += 1
        elif p.kind is inspect.Parameter.KEYWORD_ONLY:
            if p.default is inspect.Parameter.empty:
                has_required_kw = True
    return {"required_positional": required,
            "fixed_positional_count": fixed,
            "max_positional": None if has_var_positional else fixed,
            "has_var_positional": has_var_positional,
            "has_required_kwargs": has_required_kw,
            "inspectable": True}


def _cached_generator_meta(gen) -> dict:
    """Cached introspection lookup keyed by callable identity.

    - Identity keying (not object equality) prevents two distinct
      callables that compare equal via custom ``__eq__`` from sharing
      stale metadata.
    - The ``_IdentityKey`` wrapper holds a strong reference, so Python
      can't recycle ``id(gen)`` for a different callable after garbage
      collection.
    - Bounded size with simple insertion-order eviction keeps the cache
      from growing without bound in long-lived processes that create
      many fresh callables (e.g., test sessions building lambdas in
      loops). Dropped wrappers release their callables for gc.
    """
    key = _IdentityKey(gen)
    cached = _GENERATOR_META_CACHE.get(key)
    if cached is not None:
        return cached
    meta = _generator_meta(gen)
    if len(_GENERATOR_META_CACHE) >= _GENERATOR_META_CACHE_MAX:
        for stale in list(_GENERATOR_META_CACHE)[: _GENERATOR_META_CACHE_MAX // 2]:
            del _GENERATOR_META_CACHE[stale]
    _GENERATOR_META_CACHE[key] = meta
    return meta


def _call_generator_within_span(generator: Callable, ts: datetime.datetime, col: int,
                                t_within: float, span_idx: int,
                                rng: "np.random.RandomState" = None):
    """Call a span-path generator with either the 5-arg or 2-arg shape.

    Dispatch by REQUIRED positional count, not by maximum callability. A
    generator like ``(ts, col, scale=1.0, factor=2.0, baseline=0.0)`` is
    callable with 5 args at the Python language level, but the author named
    the 3rd–5th positions for their own values, not for runtime internals.
    Calling 5-arg would silently bind ``t_within``/``span_idx``/``rng`` to
    those parameters. Required-based dispatch instead calls 2-arg, keeps
    the defaults, and avoids the misbind. Only generators that explicitly
    opt into the runtime internals (``required=5`` or ``*args``) receive
    the 5-arg call.

    Uninspectable callables (e.g., C extensions) fall back to a try/except
    chain that tries only the two canonical shapes (5-arg then 2-arg) — no
    intermediate 3- or 4-arg attempts, because those would themselves be
    misbinding vectors. The fallback retries only on a call-*binding*
    TypeError; a TypeError raised inside the generator body propagates
    (see the step-path fallback in ``_resolve_anomaly_value``).
    """
    meta = _cached_generator_meta(generator)
    if not meta["inspectable"]:
        # See the matching step-path fallback in ``_resolve_anomaly_value``
        # for the binding-vs-body TypeError distinction.
        try:
            return generator(ts, col, t_within, span_idx, rng)
        except TypeError as exc:
            if exc.__traceback__.tb_next is not None:
                raise
            return generator(ts, col)
    required = meta["required_positional"]
    fixed = meta["fixed_positional_count"]
    if meta["has_var_positional"]:
        # Mirror the validator's *args misbind checks for direct callers.
        # Two distinct misbind cases:
        #   (a) required <= 2 with default-having fixed positions beyond
        #       (ts, col): the 5-arg call overwrites declared defaults at
        #       positions 3 through min(fixed, 5).
        #   (b) required ∈ {3, 4}: the 5-arg call binds t_within (and
        #       possibly span_idx) into REQUIRED positional slots the
        #       author intended for other values (e.g. (ts, col, rng,
        #       *args) where rng would receive t_within).
        if required <= 2 and fixed > 2:
            misbind_end = min(fixed, 5)
            misbind_range = (
                f"position 3" if misbind_end == 3
                else f"positions 3 through {misbind_end}"
            )
            raise TypeError(
                f"Generator {generator!r} has *args with "
                f"fixed_positional_count={fixed} > 2 and required <= 2; "
                f"the 5-arg span call would overwrite the default-having "
                f"fixed positional at {misbind_range}. Use (ts, col) or "
                f"(ts, col, *args) instead."
            )
        if required > 2 and required != 5:
            raise TypeError(
                f"Generator {generator!r} has *args with "
                f"required_positional={required} (neither 2 nor 5); "
                f"the 5-arg span call would bind t_within/span_idx into "
                f"the required positions the author intended for other "
                f"values. Use (ts, col, t_within, span_idx, rng) for full "
                f"control or (ts, col) for the legacy form."
            )
        return generator(ts, col, t_within, span_idx, rng)
    if required == 5:
        return generator(ts, col, t_within, span_idx, rng)
    if required <= 2:
        return generator(ts, col)
    raise TypeError(
        f"Generator {generator!r} requires {required} positional args; "
        f"span-path specs must use a 2-arg or 5-arg required shape. "
        f"_validate_scenario_spec should have rejected this at import time."
    )


def _span_fraction(t_within: float, duration_seconds: float) -> float:
    if duration_seconds <= 0:
        return 1.0
    return min(max(t_within / duration_seconds, 0.0), 1.0)
