"""HTTP simulator server for Kubernetes/Helm incident debugging.

The module is intentionally stdlib-only. Server mode should be useful in a
fresh editable install without adding a web framework dependency to the core
generator.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import csv
import datetime as _dt
import gzip
import hmac
import ipaddress
import json
import shlex
import sqlite3
import sys
import tempfile
import threading
import time
import urllib.parse
from collections import Counter, deque
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


DEFAULT_NAMESPACE = "saas-prod"
DEFAULT_RELEASE = "simulated-saas"
DEFAULT_CHART = "simulated-saas-0.3.0"
DEFAULT_TRACE_LIMIT = 500
DEFAULT_MAX_BODY_BYTES = 1024 * 1024


@dataclass(frozen=True)
class ServerSecurityConfig:
    """HTTP boundary controls for server mode."""

    auth_token: str = ""
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES
    allow_remote_without_auth: bool = False


@dataclass(frozen=True)
class OpsComponentImpact:
    """Per-component Kubernetes health overlay for an active scenario."""

    component: str
    deployment_status: str = "ScenarioInfluenced"
    pod_status: str = "Running"
    ready: str = ""
    ready_replicas: int | None = None
    ready_replicas_delta: int = 0
    restarts: int = 0
    cpu_pct: int | None = None
    cpu_m: int | None = None
    memory_mi: int | None = None
    memory_pct: int | None = None
    pvc_used_pct: int | None = None


@dataclass(frozen=True)
class OpsScenarioProfile:
    """Kubernetes/Helm behavior overlay for one anomaly scenario."""

    scenario_id: str
    affected_components: tuple[str, ...]
    summary: str
    events: tuple[str, ...]
    logs: tuple[str, ...]
    impacts: tuple[OpsComponentImpact, ...] = ()
    helm_notes: str = ""
    rollout_note: str = ""


def _impact(
    component: str,
    deployment_status: str = "ScenarioInfluenced",
    *,
    pod_status: str = "Running",
    ready: str = "",
    ready_replicas: int | None = None,
    ready_replicas_delta: int = 0,
    restarts: int = 0,
    cpu_pct: int | None = None,
    cpu_m: int | None = None,
    memory_mi: int | None = None,
    memory_pct: int | None = None,
    pvc_used_pct: int | None = None,
) -> OpsComponentImpact:
    return OpsComponentImpact(
        component=component,
        deployment_status=deployment_status,
        pod_status=pod_status,
        ready=ready,
        ready_replicas=ready_replicas,
        ready_replicas_delta=ready_replicas_delta,
        restarts=restarts,
        cpu_pct=cpu_pct,
        cpu_m=cpu_m,
        memory_mi=memory_mi,
        memory_pct=memory_pct,
        pvc_used_pct=pvc_used_pct,
    )


def _profile(
    scenario_id: str,
    affected_components: tuple[str, ...],
    summary: str,
    events: tuple[str, ...],
    logs: tuple[str, ...],
    impacts: tuple[OpsComponentImpact, ...],
    *,
    helm_notes: str = "",
    rollout_note: str = "",
) -> OpsScenarioProfile:
    return OpsScenarioProfile(
        scenario_id=scenario_id,
        affected_components=affected_components,
        summary=summary,
        events=events,
        logs=logs,
        impacts=impacts,
        helm_notes=helm_notes or f"{summary}. Start with kubectl get pods, kubectl get events, and service logs.",
        rollout_note=rollout_note,
    )


OPS_SCENARIO_PROFILES: dict[str, OpsScenarioProfile] = {
    "auth_brute_force": _profile(
        "auth_brute_force",
        ("authservice", "apigateway"),
        "auth pods are absorbing a brute-force login burst",
        (
            "Warning AuthThrottle authservice login throttle engaged for repeated failures",
            "Warning RateLimited apigateway rejected excess login attempts",
            "Normal HPA authservice replica targets increased for CPU pressure",
        ),
        (
            "authservice failed_login_rate elevated from repeated credential attempts",
            "authservice token bucket limiting username spray source ranges",
            "apigateway login route returning 429 for abusive clients",
        ),
        (
            _impact("authservice", "RateLimited", cpu_pct=82, cpu_m=820, memory_mi=720, memory_pct=66),
            _impact("apigateway", "RateLimited", cpu_pct=76, cpu_m=760, memory_mi=680),
        ),
    ),
    "cache_collapse": _profile(
        "cache_collapse",
        ("cacheservice", "database"),
        "cache hit ratio collapsed and database read pressure is rising",
        (
            "Warning CacheMissStorm cacheservice hit ratio fell below 35 percent",
            "Warning BackendPressure database read latency elevated after cache misses",
            "Normal HPA cacheservice tracking memory pressure",
        ),
        (
            "cacheservice hit_ratio=0.31 eviction pressure elevated",
            "cacheservice hot keys bypassed local shard cache",
            "database read pool saturated by cache miss amplification",
        ),
        (
            _impact("cacheservice", "CacheDegraded", cpu_pct=73, cpu_m=730, memory_mi=1700, memory_pct=91),
            _impact("database", "ReadPressure", cpu_pct=74, cpu_m=740, memory_mi=1300, memory_pct=64),
        ),
    ),
    "api_cpu_saturation": _profile(
        "api_cpu_saturation",
        ("apigateway", "authservice", "cacheservice"),
        "API gateway CPU saturation is causing retry amplification",
        (
            "Warning CPUSaturated apigateway CPU above request shedding threshold",
            "Warning RetryStorm apigateway retries increased against auth and cache backends",
            "Normal Scaling apigateway HPA evaluating additional replicas",
        ),
        (
            "apigateway worker CPU saturated, p95 latency elevated",
            "apigateway retry budget consumed for authservice upstream",
            "cacheservice saw duplicate lookup pressure from gateway retries",
        ),
        (
            _impact("apigateway", "CPUSaturated", cpu_pct=94, cpu_m=940, memory_mi=760, memory_pct=70),
            _impact("authservice", "RetryPressure", cpu_pct=68, cpu_m=680),
            _impact("cacheservice", "RetryPressure", cpu_pct=66, cpu_m=660, memory_mi=980),
        ),
    ),
    "db_stall": _profile(
        "db_stall",
        ("database", "apigateway", "authservice", "mqservice"),
        "database read stalls are backing up synchronous request paths",
        (
            "Warning DatabaseStall database query executor stalled during backup window",
            "Warning UpstreamLatency apigateway database upstream latency above SLO",
            "Warning QueueBacklog mqservice retries accumulating for database writes",
        ),
        (
            "database read latency exceeded 4800ms during backup window",
            "apigateway upstream database request timed out after retry",
            "mqservice pending writes waiting on database acknowledgements",
        ),
        (
            _impact("database", "DatabaseStall", cpu_pct=83, cpu_m=830, memory_mi=1550, memory_pct=72, pvc_used_pct=86),
            _impact("apigateway", "UpstreamLatency", cpu_pct=64, cpu_m=640),
            _impact("authservice", "UpstreamLatency", cpu_pct=61, cpu_m=610),
            _impact("mqservice", "QueueBacklog", cpu_pct=70, cpu_m=700, memory_mi=900),
        ),
    ),
    "mq_jam": _profile(
        "mq_jam",
        ("mqservice", "apigateway", "database", "authservice"),
        "message queue backlog is delaying async work and retries",
        (
            "Warning QueueBacklog mqservice pending messages above drain threshold",
            "Warning ConsumerLag scheduler consumers are behind producer rate",
            "Normal Backpressure apigateway throttling async enqueue calls",
        ),
        (
            "mqservice queue_depth=18420 consumer_lag_seconds=740",
            "mqservice broker publish confirms slowed by disk flush latency",
            "apigateway async request queue returned retry-after headers",
        ),
        (
            _impact("mqservice", "QueueBacklog", cpu_pct=80, cpu_m=800, memory_mi=1450, memory_pct=78),
            _impact("apigateway", "Backpressure", cpu_pct=63, cpu_m=630),
            _impact("database", "WriteBacklog", cpu_pct=65, cpu_m=650),
            _impact("authservice", "RetryPressure", cpu_pct=58, cpu_m=580),
        ),
    ),
    "lb_flapping": _profile(
        "lb_flapping",
        ("loadbalancer", "apigateway"),
        "load balancer health checks are flapping gateway endpoints",
        (
            "Warning HealthCheckFlap loadbalancer removed apigateway endpoints intermittently",
            "Warning TLSError loadbalancer TLS handshake failures increased",
            "Normal EndpointSlice apigateway endpoints restored after check pass",
        ),
        (
            "loadbalancer target health check failed then recovered",
            "loadbalancer tls_handshake_error_rate above normal threshold",
            "apigateway saw uneven traffic after endpoint churn",
        ),
        (
            _impact("loadbalancer", "HealthCheckFlap", cpu_pct=67, cpu_m=670, memory_mi=520),
            _impact("apigateway", "EndpointChurn", cpu_pct=69, cpu_m=690),
        ),
    ),
    "object_store_5xx": _profile(
        "object_store_5xx",
        ("objectstore", "apigateway"),
        "object store 5xx responses are slowing upload and download paths",
        (
            "Warning ObjectStore5xx objectstore upstream 5xx rate above threshold",
            "Warning BandwidthSaturation objectstore egress saturation detected",
            "Normal Retry apigateway retrying object storage requests with backoff",
        ),
        (
            "objectstore returned elevated 503s for PUT requests",
            "objectstore bandwidth_mbps near configured ceiling",
            "apigateway object storage upstream returned retryable 5xx",
        ),
        (
            _impact("objectstore", "ObjectStore5xx", cpu_pct=72, cpu_m=720, memory_mi=920),
            _impact("apigateway", "Upstream5xx", cpu_pct=64, cpu_m=640),
        ),
    ),
    "vectorstore_pressure": _profile(
        "vectorstore_pressure",
        ("vectorstore", "llm_analytics"),
        "vector index rebuild pressure is degrading retrieval quality",
        (
            "Warning IndexRebuild vectorstore rebuild consuming query capacity",
            "Warning RecallDegraded llm_analytics retrieval recall below target",
            "Normal Throttle vectorstore background compaction throttled",
        ),
        (
            "vectorstore index rebuild phase=merge query_latency_ms=2210",
            "vectorstore recall sample dropped below relevance threshold",
            "llm_analytics retrieval context returned fewer high-score chunks",
        ),
        (
            _impact("vectorstore", "IndexRebuild", cpu_pct=88, cpu_m=880, memory_mi=2100, memory_pct=89),
            _impact("llm_analytics", "RetrievalDegraded", cpu_pct=70, cpu_m=700, memory_mi=1400),
        ),
    ),
    "scheduler_overflow": _profile(
        "scheduler_overflow",
        ("scheduler", "database"),
        "scheduler queue overflow is delaying background jobs",
        (
            "Warning JobOverrun scheduler active job exceeded expected runtime",
            "Warning QueueOverflow scheduler pending jobs above concurrency cap",
            "Normal LeaseRenewed scheduler leader lease remains healthy",
        ),
        (
            "scheduler job runtime exceeded max expected duration",
            "scheduler pending_jobs=940 worker_pool_saturated=true",
            "database advisory lock wait increased for scheduled tasks",
        ),
        (
            _impact("scheduler", "QueueOverflow", cpu_pct=84, cpu_m=840, memory_mi=980, memory_pct=74),
            _impact("database", "LockWait", cpu_pct=66, cpu_m=660),
        ),
    ),
    "payment_5xx": _profile(
        "payment_5xx",
        ("paymentservice", "apigateway"),
        "payment provider 5xx responses are surfacing at checkout",
        (
            "Warning Provider5xx paymentservice upstream processor returned 5xx responses",
            "Warning FraudRule paymentservice fraud rule misfire increased declines",
            "Normal CircuitBreaker apigateway checkout circuit breaker half-open",
        ),
        (
            "paymentservice processor_http_5xx_rate above checkout threshold",
            "paymentservice fraud rule rejected known-good payment attempts",
            "apigateway checkout route returned degraded payment status",
        ),
        (
            _impact("paymentservice", "Provider5xx", cpu_pct=68, cpu_m=680, memory_mi=760),
            _impact("apigateway", "CheckoutDegraded", cpu_pct=62, cpu_m=620),
        ),
    ),
    "idp_jwks_storm": _profile(
        "idp_jwks_storm",
        ("identityprovider", "authservice"),
        "identity provider JWKS cache misses are slowing token validation",
        (
            "Warning JWKSCacheMiss identityprovider JWKS cache misses increased sharply",
            "Warning TokenValidation authservice token validation latency above SLO",
            "Normal CacheWarmup authservice refreshing JWKS cache entries",
        ),
        (
            "identityprovider jwks_cache_miss_rate above threshold",
            "authservice token validation waited on remote JWKS fetch",
            "authservice accepted cached keys after warmup retry",
        ),
        (
            _impact("identityprovider", "JWKSCacheMiss", cpu_pct=76, cpu_m=760, memory_mi=820),
            _impact("authservice", "TokenValidationSlow", cpu_pct=70, cpu_m=700),
        ),
    ),
    "observability_lag": _profile(
        "observability_lag",
        ("observabilitypipeline", "mqservice"),
        "observability ingest lag and label cardinality are elevated",
        (
            "Warning IngestLag observabilitypipeline ingest lag above 240s",
            "Warning CardinalityStorm observabilitypipeline label cardinality increased",
            "Warning QueueBacklog mqservice telemetry queue depth above threshold",
        ),
        (
            "observabilitypipeline ingest_lag_seconds=247 cardinality_labels elevated",
            "observabilitypipeline dropped high-cardinality metric batch",
            "mqservice telemetry topic consumer lag increased",
        ),
        (
            _impact("observabilitypipeline", "IngestLag", cpu_pct=87, cpu_m=870, memory_mi=1800, memory_pct=86),
            _impact("mqservice", "TelemetryBacklog", cpu_pct=72, cpu_m=720, memory_mi=1100),
        ),
    ),
    "monday_baseline": _profile(
        "monday_baseline",
        ("authservice", "apigateway"),
        "normal Monday login burst is increasing request volume",
        (
            "Normal TrafficBurst authservice Monday login burst above weekend baseline",
            "Normal Scaling apigateway HPA observed healthy traffic increase",
            "Normal SLO apigateway latency remains within target",
        ),
        (
            "authservice login burst within expected Monday envelope",
            "apigateway request rate increased without error budget burn",
            "authservice sessions created at elevated but healthy rate",
        ),
        (
            _impact("authservice", "TrafficBurst", cpu_pct=61, cpu_m=610, memory_mi=640),
            _impact("apigateway", "TrafficBurst", cpu_pct=64, cpu_m=640, memory_mi=620),
        ),
        helm_notes="Traffic is elevated but healthy. Confirm autoscaling with kubectl get hpa and watch gateway/auth latency.",
        rollout_note="traffic burst is expected; rollout state is healthy",
    ),
    "llm_viral_surge_day2": _profile(
        "llm_viral_surge_day2",
        ("llm_analytics", "database", "cacheservice", "apigateway"),
        "viral LLM usage surge is driving analytics and cache pressure",
        (
            "Warning LLMRequestSurge llm_analytics request volume exceeded forecast",
            "Warning CachePressure cacheservice context cache memory pressure elevated",
            "Normal Scaling apigateway routing additional LLM traffic",
        ),
        (
            "llm_analytics request fanout elevated after customer demo traffic",
            "cacheservice context cache eviction rate increased",
            "database prompt metadata writes elevated during viral surge",
        ),
        (
            _impact("llm_analytics", "LLMSurge", cpu_pct=86, cpu_m=860, memory_mi=2300, memory_pct=86),
            _impact("database", "WritePressure", cpu_pct=70, cpu_m=700),
            _impact("cacheservice", "CachePressure", cpu_pct=72, cpu_m=720, memory_mi=1650, memory_pct=84),
            _impact("apigateway", "TrafficSurge", cpu_pct=75, cpu_m=750),
        ),
    ),
    "llm_enterprise_onboarding": _profile(
        "llm_enterprise_onboarding",
        ("llm_analytics", "vectorstore", "database", "cacheservice"),
        "enterprise onboarding is using large LLM contexts and vector lookups",
        (
            "Warning LargeContext llm_analytics context window size above baseline",
            "Warning VectorLookup vectorstore query latency elevated for onboarding tenant",
            "Normal TenantRamp database onboarding tenant write volume elevated",
        ),
        (
            "llm_analytics context_tokens p95 elevated for enterprise tenant",
            "vectorstore hybrid search latency increased during onboarding import",
            "database tenant metadata import running at elevated write volume",
        ),
        (
            _impact("llm_analytics", "LargeContext", cpu_pct=82, cpu_m=820, memory_mi=2600, memory_pct=88),
            _impact("vectorstore", "LookupPressure", cpu_pct=79, cpu_m=790, memory_mi=2200, memory_pct=83),
            _impact("database", "TenantImport", cpu_pct=68, cpu_m=680),
            _impact("cacheservice", "ContextCachePressure", cpu_pct=69, cpu_m=690, memory_mi=1550),
        ),
    ),
    "llm_rate_limit_fallout": _profile(
        "llm_rate_limit_fallout",
        ("llm_analytics", "apigateway"),
        "LLM provider rate limits are causing retries and fallback responses",
        (
            "Warning ProviderRateLimited llm_analytics upstream provider returned 429",
            "Warning RetryBudget apigateway LLM route retry budget nearly exhausted",
            "Normal Fallback llm_analytics served cached fallback response",
        ),
        (
            "llm_analytics provider_429_rate above fallback threshold",
            "llm_analytics retry-after honored for upstream provider",
            "apigateway llm route returning degraded fallback metadata",
        ),
        (
            _impact("llm_analytics", "ProviderRateLimited", cpu_pct=66, cpu_m=660, memory_mi=1500),
            _impact("apigateway", "FallbackServing", cpu_pct=58, cpu_m=580),
        ),
    ),
    "llm_weekend_batch": _profile(
        "llm_weekend_batch",
        ("llm_analytics", "objectstore", "database", "cacheservice"),
        "weekend LLM batch analytics job is consuming storage and cache capacity",
        (
            "Warning BatchPressure llm_analytics weekend batch job consuming worker capacity",
            "Warning ObjectStoreBandwidth objectstore batch artifact bandwidth elevated",
            "Warning CacheEviction cacheservice batch feature cache evictions increased",
        ),
        (
            "llm_analytics weekend batch worker pool at high utilization",
            "objectstore batch artifact upload throughput near limit",
            "database batch analytics checkpoints increased write pressure",
        ),
        (
            _impact("llm_analytics", "BatchPressure", cpu_pct=90, cpu_m=900, memory_mi=2400, memory_pct=84),
            _impact("objectstore", "BandwidthPressure", cpu_pct=74, cpu_m=740, memory_mi=980),
            _impact("database", "BatchWritePressure", cpu_pct=72, cpu_m=720, pvc_used_pct=78),
            _impact("cacheservice", "BatchEvictions", cpu_pct=70, cpu_m=700, memory_mi=1500),
        ),
    ),
    "llm_second_viral": _profile(
        "llm_second_viral",
        ("llm_analytics", "apigateway", "database", "cacheservice"),
        "second viral LLM event is pushing gateway and analytics capacity",
        (
            "Warning ViralTraffic llm_analytics second viral event exceeded capacity forecast",
            "Warning GatewayPressure apigateway LLM route latency elevated",
            "Normal Scaling cacheservice HPA observing context cache pressure",
        ),
        (
            "llm_analytics viral traffic from social mention exceeded forecast",
            "apigateway llm route p95 latency elevated under viral surge",
            "cacheservice context cache hot keys churned during viral event",
        ),
        (
            _impact("llm_analytics", "ViralTraffic", cpu_pct=92, cpu_m=920, memory_mi=2500, memory_pct=87),
            _impact("apigateway", "GatewayPressure", cpu_pct=86, cpu_m=860, memory_mi=780),
            _impact("database", "MetadataWritePressure", cpu_pct=74, cpu_m=740),
            _impact("cacheservice", "HotKeyChurn", cpu_pct=78, cpu_m=780, memory_mi=1700),
        ),
    ),
    "gpu_inference_fragmentation": _profile(
        "gpu_inference_fragmentation",
        ("gpu_inference", "llm_analytics"),
        "GPU inference allocator fragmentation is causing sparse failures",
        (
            "Warning GPUFragmentation gpu_inference allocator fragmentation above threshold",
            "Warning SparseInferenceFailure gpu_inference returned intermittent allocation failures",
            "Normal Fallback llm_analytics routed some requests to CPU fallback",
        ),
        (
            "gpu_inference cuda allocator fragmentation caused allocation retry",
            "gpu_inference sparse batch failed then succeeded after compaction",
            "llm_analytics routed high-priority inference through fallback pool",
        ),
        (
            _impact("gpu_inference", "GPUFragmented", pod_status="CrashLoopBackOff", ready="0/1", ready_replicas=0, restarts=2, cpu_pct=77, cpu_m=770, memory_mi=3100, memory_pct=92),
            _impact("llm_analytics", "InferenceFallback", cpu_pct=78, cpu_m=780, memory_mi=1800),
        ),
    ),
    "regional_failover_storm": _profile(
        "regional_failover_storm",
        ("loadbalancer", "apigateway", "database", "authservice", "mqservice"),
        "regional failover storm is driving cross-service saturation",
        (
            "Warning RegionalFailover loadbalancer shifted traffic after regional health loss",
            "Warning CrossRegionLatency database cross-region replication latency elevated",
            "Warning QueueBacklog mqservice failover replay backlog increased",
        ),
        (
            "loadbalancer regional failover active, traffic shifted to surviving region",
            "database replication lag elevated during regional failover",
            "mqservice replay backlog increased after failover drain",
        ),
        (
            _impact("loadbalancer", "RegionalFailover", cpu_pct=91, cpu_m=910, memory_mi=780),
            _impact("apigateway", "FailoverSaturated", cpu_pct=90, cpu_m=900, memory_mi=860),
            _impact("database", "ReplicationLag", cpu_pct=88, cpu_m=880, memory_mi=1700),
            _impact("authservice", "FailoverPressure", cpu_pct=79, cpu_m=790),
            _impact("mqservice", "ReplayBacklog", cpu_pct=82, cpu_m=820, memory_mi=1300),
        ),
    ),
    "cache_db_meltdown": _profile(
        "cache_db_meltdown",
        ("cacheservice", "database", "llm_analytics", "apigateway"),
        "cache and database are failing together and amplifying LLM latency",
        (
            "Warning CacheDBMeltdown cacheservice miss storm and database write latency are both elevated",
            "Warning OOMRisk cacheservice memory pressure near limit",
            "Warning DatabasePressure database connection pool saturated",
        ),
        (
            "cacheservice miss storm exhausted backend database capacity",
            "database connection pool saturated under cache miss amplification",
            "llm_analytics prompt hydration slowed by cache and database pressure",
        ),
        (
            _impact("cacheservice", "Degraded", pod_status="CrashLoopBackOff", ready="0/1", ready_replicas=0, restarts=5, cpu_pct=88, cpu_m=880, memory_mi=2100, memory_pct=97),
            _impact("database", "StoragePressure", cpu_pct=92, cpu_m=920, memory_mi=1900, memory_pct=82, pvc_used_pct=91),
            _impact("llm_analytics", "DependencyDegraded", cpu_pct=76, cpu_m=760),
            _impact("apigateway", "DependencyDegraded", cpu_pct=75, cpu_m=750),
        ),
    ),
    "llm_provider_outage": _profile(
        "llm_provider_outage",
        ("llm_analytics", "apigateway", "cacheservice"),
        "LLM provider outage is forcing cached fallback responses",
        (
            "Warning ProviderUnavailable llm_analytics upstream provider health check failed",
            "Warning FallbackExhaustion cacheservice fallback cache hit ratio falling",
            "Warning GatewayDegraded apigateway LLM route returning degraded responses",
        ),
        (
            "llm_analytics provider unavailable after repeated health check failures",
            "cacheservice fallback response cache nearing exhaustion",
            "apigateway llm route served degraded provider outage response",
        ),
        (
            _impact("llm_analytics", "ProviderUnavailable", cpu_pct=62, cpu_m=620, memory_mi=1500),
            _impact("apigateway", "ProviderOutage", cpu_pct=60, cpu_m=600),
            _impact("cacheservice", "FallbackPressure", cpu_pct=66, cpu_m=660, memory_mi=1500),
        ),
    ),
    "gateway_ddos": _profile(
        "gateway_ddos",
        ("apigateway", "authservice", "database", "mqservice"),
        "gateway and downstream services are rate-limiting a request flood",
        (
            "Warning RateLimited apigateway rejecting excess client requests",
            "Normal Scaling apigateway HPA increased replicas for CPU pressure",
            "Warning Saturated mqservice and database seeing retry amplification",
        ),
        (
            "apigateway rate_limited_per_sec above threshold",
            "authservice authentication route protected by gateway shedding",
            "mqservice async queue pressure increased from gateway flood labels",
        ),
        (
            _impact("apigateway", "RateLimited", cpu_pct=96, cpu_m=960, memory_mi=860, memory_pct=76),
            _impact("authservice", "RateLimited", cpu_pct=83, cpu_m=830),
            _impact("database", "RetryPressure", cpu_pct=78, cpu_m=780),
            _impact("mqservice", "RetryPressure", cpu_pct=80, cpu_m=800, memory_mi=1250),
        ),
    ),
    "storage_layer_pressure": _profile(
        "storage_layer_pressure",
        ("objectstore", "database", "apigateway"),
        "storage layer pressure is increasing PUT latency and 5xx responses",
        (
            "Warning StoragePressure objectstore PUT latency above SLO",
            "Warning DatabaseWritePressure database write latency elevated from storage waits",
            "Warning Upstream5xx apigateway storage-backed route returning 5xx",
        ),
        (
            "objectstore put_latency_ms p95 above threshold",
            "database storage wait time elevated during object PUT pressure",
            "apigateway upload path observed upstream storage 5xx",
        ),
        (
            _impact("objectstore", "StoragePressure", cpu_pct=84, cpu_m=840, memory_mi=1100, pvc_used_pct=84),
            _impact("database", "StorageWait", cpu_pct=82, cpu_m=820, memory_mi=1600, pvc_used_pct=88),
            _impact("apigateway", "UploadDegraded", cpu_pct=69, cpu_m=690),
        ),
    ),
    "deploy_bad_canary_rollback": _profile(
        "deploy_bad_canary_rollback",
        ("apigateway", "authservice", "cacheservice", "database"),
        "bad canary revision rolled back after readiness and error-rate failures",
        (
            "Warning Unhealthy apigateway-canary readiness probe failed: HTTP 503",
            "Warning FailedRollout deployment/apigateway exceeded progress deadline",
            "Normal Rollback helm release simulated-saas rolled back to previous revision",
        ),
        (
            "apigateway canary build rejected 18 percent of requests",
            "apigateway rollback controller restored stable revision",
            "authservice saw transient login success dip during canary window",
        ),
        (
            _impact("apigateway", "RolledBack", cpu_pct=63, cpu_m=630, memory_mi=620, restarts=1),
            _impact("authservice", "RecoveredAfterRollback", cpu_pct=57, cpu_m=570),
            _impact("cacheservice", "RecoveredAfterRollback", cpu_pct=55, cpu_m=550),
            _impact("database", "RecoveredAfterRollback", cpu_pct=54, cpu_m=540),
        ),
        helm_notes="Canary revision failed health checks and was rolled back to the stable deployment.",
        rollout_note="release was rolled back from failed canary revision",
    ),
    "dns_provider_outage": _profile(
        "dns_provider_outage",
        ("loadbalancer", "apigateway", "identityprovider", "paymentservice"),
        "external DNS resolution failures are surfacing as upstream errors",
        (
            "Warning DNSConfigForming loadbalancer upstream resolver errors increased",
            "Warning Unhealthy apigateway readiness probe saw DNS lookup timeout",
            "Warning ExternalDependency identityprovider and paymentservice DNS lookups timing out",
        ),
        (
            "loadbalancer resolver error rate elevated for external dependencies",
            "apigateway lookup identity provider failed: i/o timeout",
            "paymentservice external processor DNS resolution failed",
        ),
        (
            _impact("loadbalancer", "DNSDegraded", cpu_pct=74, cpu_m=740, memory_mi=700),
            _impact("apigateway", "DNSDependencyFailure", cpu_pct=71, cpu_m=710),
            _impact("identityprovider", "DNSDependencyFailure", cpu_pct=66, cpu_m=660),
            _impact("paymentservice", "DNSDependencyFailure", cpu_pct=65, cpu_m=650),
        ),
    ),
    "network_partition_az_split": _profile(
        "network_partition_az_split",
        ("database", "mqservice", "apigateway", "authservice"),
        "one AZ is partitioned and replication plus cross-AZ RPCs are degraded",
        (
            "Warning NodeNotReady ip-10-0-3-42 node controller marked node unreachable",
            "Warning NetworkPartition database replication links split across availability zones",
            "Warning FailedScheduling scheduler cannot place pods in isolated AZ",
        ),
        (
            "database replication heartbeat missed from partitioned zone",
            "mqservice broker heartbeats missed from partitioned zone",
            "apigateway retrying upstream requests after zone split",
        ),
        (
            _impact("database", "NetworkPartition", cpu_pct=82, cpu_m=820, memory_mi=1600),
            _impact("mqservice", "NetworkPartition", cpu_pct=78, cpu_m=780, memory_mi=1200),
            _impact("apigateway", "NetworkDegraded", cpu_pct=70, cpu_m=700),
            _impact("authservice", "NetworkDegraded", cpu_pct=67, cpu_m=670),
        ),
    ),
    "cache_leak_restart": _profile(
        "cache_leak_restart",
        ("cacheservice", "database", "apigateway", "mqservice"),
        "cache memory leak is driving OOM restarts and miss pressure",
        (
            "Warning OOMKilling cacheservice-0 container killed after memory limit pressure",
            "Warning BackOff cacheservice-0 restarting failed container",
            "Normal Pulled cacheservice-0 pulled cached image after restart",
        ),
        (
            "cacheservice heap watermark exceeded, triggering eviction storm",
            "cacheservice process restarted after OOMKilled",
            "database query volume elevated because cache misses increased",
        ),
        (
            _impact("cacheservice", "Degraded", pod_status="CrashLoopBackOff", ready="0/1", ready_replicas=0, restarts=7, cpu_pct=72, cpu_m=720, memory_mi=1900, memory_pct=96),
            _impact("database", "CacheMissPressure", cpu_pct=66, cpu_m=660),
            _impact("apigateway", "CacheMissPressure", cpu_pct=64, cpu_m=640),
            _impact("mqservice", "CacheMissPressure", cpu_pct=60, cpu_m=600),
        ),
    ),
    "jwks_rotation_chaos": _profile(
        "jwks_rotation_chaos",
        ("loadbalancer", "identityprovider", "authservice", "apigateway", "paymentservice", "cacheservice"),
        "certificate and JWKS rotation are causing authentication instability",
        (
            "Warning CertRotation loadbalancer certificate reload produced transient TLS failures",
            "Warning JWKSRotation identityprovider key rotation cache mismatch detected",
            "Warning TokenValidation authservice rejected tokens signed by stale key cache",
        ),
        (
            "loadbalancer certificate reload completed with transient handshake failures",
            "identityprovider rotated JWKS while cache entries were stale",
            "authservice token validation failed against stale JWKS kid",
        ),
        (
            _impact("loadbalancer", "CertRotation", cpu_pct=70, cpu_m=700, memory_mi=700),
            _impact("identityprovider", "JWKSRotation", cpu_pct=74, cpu_m=740, memory_mi=880),
            _impact("authservice", "TokenValidationFailing", ready_replicas_delta=-1, restarts=1, cpu_pct=76, cpu_m=760),
            _impact("apigateway", "AuthDependencyDegraded", cpu_pct=68, cpu_m=680),
            _impact("paymentservice", "AuthDependencyDegraded", cpu_pct=63, cpu_m=630),
            _impact("cacheservice", "JWKSCacheChurn", cpu_pct=64, cpu_m=640, memory_mi=1400),
        ),
    ),
    "db_disk_exhaustion": _profile(
        "db_disk_exhaustion",
        ("database", "scheduler", "observabilitypipeline", "mqservice", "apigateway"),
        "database PVC pressure and write latency are elevated",
        (
            "Warning VolumePressure database-0 write latency elevated while pvc database-data is 92% full",
            "Warning Backoff database-0 checkpoint fsync latency above normal threshold",
            "Normal Scaling scheduler queue workers holding steady while database latency recovers",
        ),
        (
            "database write checkpoint took 8421ms, disk_used_pct=92",
            "database WAL flush delayed by storage pressure",
            "apigateway upstream database write path returned elevated latency",
        ),
        (
            _impact("database", "StoragePressure", cpu_pct=86, cpu_m=860, memory_mi=1500, memory_pct=68, pvc_used_pct=92),
            _impact("scheduler", "DatabaseBackpressure", cpu_pct=68, cpu_m=680),
            _impact("observabilitypipeline", "DatabaseBackpressure", cpu_pct=62, cpu_m=620),
            _impact("mqservice", "WriteBacklog", cpu_pct=70, cpu_m=700),
            _impact("apigateway", "DatabaseBackpressure", cpu_pct=64, cpu_m=640),
        ),
    ),
    "auth_pod_failure": _profile(
        "auth_pod_failure",
        ("authservice", "apigateway"),
        "one auth pod is failing while sibling pods remain healthy",
        (
            "Warning Unhealthy authservice-0 readiness probe failed",
            "Warning BackOff authservice-0 restarting failed container",
            "Normal EndpointSlice authservice endpoints updated after pod failure",
        ),
        (
            "authservice pod i0 returning high error_rate for login requests",
            "authservice pod i1 healthy, load balancer shifted partial traffic",
            "apigateway backend latency elevated for auth upstream",
        ),
        (
            _impact("authservice", "PartialOutage", pod_status="Error", ready="0/1", ready_replicas_delta=-1, restarts=3, cpu_pct=78, cpu_m=780),
            _impact("apigateway", "AuthDependencyDegraded", cpu_pct=68, cpu_m=680),
        ),
    ),
    "cache_az_isolation": _profile(
        "cache_az_isolation",
        ("cacheservice",),
        "cache pods in one availability zone are isolated",
        (
            "Warning NetworkUnavailable cacheservice pods in us-east-1a lost peer connectivity",
            "Warning Unhealthy cacheservice readiness probe failed for isolated AZ",
            "Normal EndpointSlice cacheservice endpoints pruned isolated addresses",
        ),
        (
            "cacheservice peer timeout to us-east-1a shard",
            "cacheservice serving degraded reads from remaining zones",
            "cacheservice isolated shard marked unavailable",
        ),
        (
            _impact("cacheservice", "AZIsolated", ready_replicas_delta=-1, restarts=1, cpu_pct=72, cpu_m=720, memory_mi=1500, memory_pct=82),
        ),
    ),
}


def validate_ops_profiles(legacy_module: Any) -> None:
    """Fail fast if an ops overlay references a missing scenario/component."""

    known_scenarios = set(legacy_module.SCENARIOS)
    known_components = set(legacy_module.COMPONENTS)
    bad_scenarios = sorted(set(OPS_SCENARIO_PROFILES) - known_scenarios)
    if bad_scenarios:
        raise RuntimeError(
            "ops scenario profiles reference unknown scenario(s): "
            + ", ".join(bad_scenarios)
        )
    missing_scenarios = sorted(known_scenarios - set(OPS_SCENARIO_PROFILES))
    if missing_scenarios:
        raise RuntimeError(
            "ops scenario profiles are missing scenario(s): "
            + ", ".join(missing_scenarios)
        )
    for profile in OPS_SCENARIO_PROFILES.values():
        bad_components = sorted(set(profile.affected_components) - known_components)
        if bad_components:
            raise RuntimeError(
                f"ops scenario profile {profile.scenario_id!r} references "
                f"unknown component(s): {', '.join(bad_components)}"
            )
        impact_components = {impact.component for impact in profile.impacts}
        bad_impact_components = sorted(impact_components - known_components)
        if bad_impact_components:
            raise RuntimeError(
                f"ops scenario profile {profile.scenario_id!r} has impact(s) for "
                f"unknown component(s): {', '.join(bad_impact_components)}"
            )
        missing_impacts = sorted(set(profile.affected_components) - impact_components)
        if missing_impacts:
            raise RuntimeError(
                f"ops scenario profile {profile.scenario_id!r} lacks impact(s) for "
                f"affected component(s): {', '.join(missing_impacts)}"
            )


@dataclass
class SimulationClock:
    """Wall-clock to synthetic-time mapping used by server mode."""

    start_time: _dt.datetime
    speedup: float
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _base_wall: float = field(default_factory=time.time)
    _base_sim: _dt.datetime = field(init=False)
    _paused: bool = False

    def __post_init__(self) -> None:
        self._base_sim = self.start_time

    def now(self) -> _dt.datetime:
        with self._lock:
            if self._paused:
                return self._base_sim
            elapsed = max(0.0, time.time() - self._base_wall) * self.speedup
            return self._base_sim + _dt.timedelta(seconds=elapsed)

    def pause(self) -> _dt.datetime:
        with self._lock:
            if not self._paused:
                elapsed = max(0.0, time.time() - self._base_wall) * self.speedup
                self._base_sim = self._base_sim + _dt.timedelta(seconds=elapsed)
                self._paused = True
            return self._base_sim

    def resume(self) -> _dt.datetime:
        with self._lock:
            self._base_wall = time.time()
            self._paused = False
            return self._base_sim

    def seek(self, timestamp: str) -> _dt.datetime:
        parsed = _parse_user_timestamp(timestamp)
        with self._lock:
            self._base_sim = parsed
            self._base_wall = time.time()
            return self._base_sim

    def to_dict(self) -> dict[str, Any]:
        return {
            "simulated_time": _format_dt(self.now()),
            "speedup": self.speedup,
            "paused": self._paused,
        }


@dataclass(frozen=True)
class ParsedCommand:
    raw_input: str
    argv: tuple[str, ...]
    family: str
    verb: str
    resource_kind: str
    resource_name: str
    namespace: str
    flags: dict[str, Any]
    positionals: tuple[str, ...]
    parse_error: str = ""


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str
    support_status: str
    matched_rule_id: str


@dataclass(frozen=True)
class KubernetesApiResponse:
    status: int
    body: Any
    content_type: str
    support_status: str
    matched_rule_id: str


@dataclass(frozen=True)
class CommandTrace:
    id: int
    received_at_wall_time: str
    simulated_time: str
    raw_input: str
    argv: tuple[str, ...]
    client: str
    command_family: str
    verb: str
    resource_kind: str
    resource_name: str
    namespace: str
    parsed_flags: dict[str, Any]
    support_status: str
    matched_rule_id: str
    active_scenarios: tuple[str, ...]
    exit_code: int
    stdout_preview: str
    stderr_preview: str
    stdout: str
    stderr: str
    latency_ms: float
    fingerprint: str
    guessed_intent: str

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CommandTrace":
        return cls(
            id=int(payload["id"]),
            received_at_wall_time=payload["received_at_wall_time"],
            simulated_time=payload["simulated_time"],
            raw_input=payload["raw_input"],
            argv=tuple(payload.get("argv", ())),
            client=payload["client"],
            command_family=payload["command_family"],
            verb=payload["verb"],
            resource_kind=payload["resource_kind"],
            resource_name=payload["resource_name"],
            namespace=payload["namespace"],
            parsed_flags=dict(payload.get("parsed_flags", {})),
            support_status=payload["support_status"],
            matched_rule_id=payload["matched_rule_id"],
            active_scenarios=tuple(payload.get("active_scenarios", ())),
            exit_code=int(payload["exit_code"]),
            stdout_preview=payload.get("stdout_preview", ""),
            stderr_preview=payload.get("stderr_preview", ""),
            stdout=payload.get("stdout", ""),
            stderr=payload.get("stderr", ""),
            latency_ms=float(payload.get("latency_ms", 0.0)),
            fingerprint=payload.get("fingerprint", ""),
            guessed_intent=payload.get("guessed_intent", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "received_at_wall_time": self.received_at_wall_time,
            "simulated_time": self.simulated_time,
            "raw_input": self.raw_input,
            "argv": list(self.argv),
            "client": self.client,
            "command_family": self.command_family,
            "verb": self.verb,
            "resource_kind": self.resource_kind,
            "resource_name": self.resource_name,
            "namespace": self.namespace,
            "parsed_flags": self.parsed_flags,
            "support_status": self.support_status,
            "matched_rule_id": self.matched_rule_id,
            "active_scenarios": list(self.active_scenarios),
            "exit_code": self.exit_code,
            "stdout_preview": self.stdout_preview,
            "stderr_preview": self.stderr_preview,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "latency_ms": self.latency_ms,
            "fingerprint": self.fingerprint,
            "guessed_intent": self.guessed_intent,
        }


class CommandTraceStore:
    """Thread-safe ring buffer plus optional JSONL/SQLite persistence."""

    def __init__(
        self,
        limit: int = DEFAULT_TRACE_LIMIT,
        persist_path: Path | None = None,
        sqlite_path: Path | None = None,
    ):
        self._limit = limit
        self._persist_path = persist_path
        self._sqlite_path = sqlite_path
        self._items: deque[CommandTrace] = deque(maxlen=limit)
        self._next_id = 1
        self._version = 0
        self._lock = threading.Lock()
        if persist_path is not None:
            persist_path.parent.mkdir(parents=True, exist_ok=True)
        if sqlite_path is not None:
            sqlite_path.parent.mkdir(parents=True, exist_ok=True)
            self._init_sqlite()
            self._load_sqlite_tail()

    @property
    def version(self) -> int:
        with self._lock:
            return self._version

    def next_id(self) -> int:
        with self._lock:
            value = self._next_id
            self._next_id += 1
            return value

    def record(self, trace: CommandTrace) -> None:
        with self._lock:
            self._items.append(trace)
            self._version += 1
            persist_path = self._persist_path
            sqlite_path = self._sqlite_path
        if persist_path is not None:
            with open(persist_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(trace.to_dict(), sort_keys=True) + "\n")
        if sqlite_path is not None:
            self._insert_sqlite(trace)

    def list(self, limit: int | None = None) -> list[dict[str, Any]]:
        if self._sqlite_path is not None:
            return self._list_sqlite(limit=limit)
        with self._lock:
            items = list(self._items)
            version = self._version
        if limit is not None:
            items = items[-limit:]
        return [
            {"version": version, **trace.to_dict()}
            for trace in reversed(items)
        ]

    def get(self, trace_id: int) -> dict[str, Any] | None:
        with self._lock:
            for trace in self._items:
                if trace.id == trace_id:
                    return trace.to_dict()
        if self._sqlite_path is not None:
            return self._get_sqlite(trace_id)
        return None

    def count(self) -> int:
        if self._sqlite_path is not None:
            with self._connect() as conn:
                row = conn.execute("SELECT count(*) FROM command_traces").fetchone()
            return int(row[0])
        with self._lock:
            return len(self._items)

    def unsupported_summary(self) -> list[dict[str, Any]]:
        if self._sqlite_path is not None:
            traces = [
                CommandTrace.from_dict(item)
                for item in self._list_sqlite(status_not="supported", limit=None)
            ]
            return _unsupported_summary_from_traces(traces)
        with self._lock:
            unsupported = [
                trace for trace in self._items
                if trace.support_status != "supported"
            ]
        return _unsupported_summary_from_traces(unsupported)

    def search(
        self,
        *,
        query: str = "",
        support_status: str = "",
        command_family: str = "",
        scenario_id: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        limit = max(1, min(limit, 500))
        offset = max(0, offset)
        if self._sqlite_path is not None:
            return self._search_sqlite(
                query=query,
                support_status=support_status,
                command_family=command_family,
                scenario_id=scenario_id,
                limit=limit,
                offset=offset,
            )
        with self._lock:
            traces = list(self._items)
            version = self._version
        filtered = [
            trace for trace in traces
            if _trace_matches_search(
                trace,
                query=query,
                support_status=support_status,
                command_family=command_family,
                scenario_id=scenario_id,
            )
        ]
        total = len(filtered)
        page = list(reversed(filtered))[offset: offset + limit]
        return {
            "items": [{"version": version, **trace.to_dict()} for trace in page],
            "total": total,
            "limit": limit,
            "offset": offset,
            "query": query,
            "support_status": support_status,
            "command_family": command_family,
            "scenario_id": scenario_id,
        }

    def _connect(self) -> sqlite3.Connection:
        if self._sqlite_path is None:
            raise RuntimeError("sqlite persistence is not configured")
        conn = sqlite3.connect(self._sqlite_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_sqlite(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS command_traces (
                    id INTEGER PRIMARY KEY,
                    received_at_wall_time TEXT NOT NULL,
                    simulated_time TEXT NOT NULL,
                    raw_input TEXT NOT NULL,
                    command_family TEXT NOT NULL,
                    verb TEXT NOT NULL,
                    resource_kind TEXT NOT NULL,
                    resource_name TEXT NOT NULL,
                    namespace TEXT NOT NULL,
                    support_status TEXT NOT NULL,
                    matched_rule_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    guessed_intent TEXT NOT NULL,
                    active_scenarios_json TEXT NOT NULL,
                    exit_code INTEGER NOT NULL,
                    stdout_preview TEXT NOT NULL,
                    stderr_preview TEXT NOT NULL,
                    stdout TEXT NOT NULL,
                    stderr TEXT NOT NULL,
                    latency_ms REAL NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_command_traces_status "
                "ON command_traces(support_status, id DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_command_traces_family "
                "ON command_traces(command_family, id DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_command_traces_fingerprint "
                "ON command_traces(fingerprint, id DESC)"
            )

    def _load_sqlite_tail(self) -> None:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, payload_json FROM command_traces ORDER BY id DESC LIMIT ?",
                (self._limit,),
            ).fetchall()
            max_row = conn.execute("SELECT max(id) AS max_id FROM command_traces").fetchone()
            count_row = conn.execute("SELECT count(*) AS total FROM command_traces").fetchone()
        traces = [
            CommandTrace.from_dict(json.loads(row["payload_json"]))
            for row in reversed(rows)
        ]
        with self._lock:
            self._items.clear()
            self._items.extend(traces)
            self._version = int(count_row["total"])
            max_id = int(max_row["max_id"] or 0)
            self._next_id = max_id + 1

    def _insert_sqlite(self, trace: CommandTrace) -> None:
        payload = trace.to_dict()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO command_traces (
                    id, received_at_wall_time, simulated_time, raw_input,
                    command_family, verb, resource_kind, resource_name,
                    namespace, support_status, matched_rule_id, fingerprint,
                    guessed_intent, active_scenarios_json, exit_code,
                    stdout_preview, stderr_preview, stdout, stderr,
                    latency_ms, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace.id,
                    trace.received_at_wall_time,
                    trace.simulated_time,
                    trace.raw_input,
                    trace.command_family,
                    trace.verb,
                    trace.resource_kind,
                    trace.resource_name,
                    trace.namespace,
                    trace.support_status,
                    trace.matched_rule_id,
                    trace.fingerprint,
                    trace.guessed_intent,
                    json.dumps(list(trace.active_scenarios), sort_keys=True),
                    trace.exit_code,
                    trace.stdout_preview,
                    trace.stderr_preview,
                    trace.stdout,
                    trace.stderr,
                    trace.latency_ms,
                    json.dumps(payload, sort_keys=True),
                ),
            )

    def _row_to_payload(self, row: sqlite3.Row) -> dict[str, Any]:
        return json.loads(row["payload_json"])

    def _list_sqlite(
        self,
        *,
        limit: int | None = None,
        status_not: str = "",
    ) -> list[dict[str, Any]]:
        params: list[Any] = []
        where = ""
        if status_not:
            where = "WHERE support_status != ?"
            params.append(status_not)
        sql = f"SELECT payload_json FROM command_traces {where} ORDER BY id DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        with self._lock:
            version = self._version
        return [{"version": version, **self._row_to_payload(row)} for row in rows]

    def _get_sqlite(self, trace_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM command_traces WHERE id = ?",
                (trace_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_payload(row)

    def _search_sqlite(
        self,
        *,
        query: str,
        support_status: str,
        command_family: str,
        scenario_id: str,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        where: list[str] = []
        params: list[Any] = []
        if query:
            like = f"%{query.lower()}%"
            where.append(
                "("
                "lower(raw_input) LIKE ? OR lower(stdout) LIKE ? OR "
                "lower(stderr) LIKE ? OR lower(fingerprint) LIKE ? OR "
                "lower(guessed_intent) LIKE ? OR lower(matched_rule_id) LIKE ?"
                ")"
            )
            params.extend([like] * 6)
        if support_status:
            where.append("support_status = ?")
            params.append(support_status)
        if command_family:
            where.append("command_family = ?")
            params.append(command_family)
        if scenario_id:
            where.append("active_scenarios_json LIKE ?")
            params.append(f"%{scenario_id}%")
        where_sql = "WHERE " + " AND ".join(where) if where else ""
        with self._connect() as conn:
            total_row = conn.execute(
                f"SELECT count(*) AS total FROM command_traces {where_sql}",
                params,
            ).fetchone()
            rows = conn.execute(
                f"SELECT payload_json FROM command_traces {where_sql} "
                "ORDER BY id DESC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        with self._lock:
            version = self._version
        return {
            "items": [{"version": version, **self._row_to_payload(row)} for row in rows],
            "total": int(total_row["total"]),
            "limit": limit,
            "offset": offset,
            "query": query,
            "support_status": support_status,
            "command_family": command_family,
            "scenario_id": scenario_id,
        }


def _unsupported_summary_from_traces(traces: list[CommandTrace]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for trace in traces:
        group = groups.setdefault(
            trace.fingerprint,
            {
                "fingerprint": trace.fingerprint,
                "count": 0,
                "first_seen": trace.received_at_wall_time,
                "last_seen": trace.received_at_wall_time,
                "examples": [],
                "guessed_intent": trace.guessed_intent,
                "support_statuses": Counter(),
            },
        )
        group["count"] += 1
        group["last_seen"] = trace.received_at_wall_time
        group["support_statuses"][trace.support_status] += 1
        if len(group["examples"]) < 5:
            group["examples"].append({
                "id": trace.id,
                "raw_input": trace.raw_input,
                "argv": list(trace.argv),
                "parsed_flags": trace.parsed_flags,
                "scenario_ids": list(trace.active_scenarios),
            })
    result = []
    for group in groups.values():
        group = dict(group)
        group["support_statuses"] = dict(group["support_statuses"])
        result.append(group)
    result.sort(key=lambda item: (-item["count"], item["fingerprint"]))
    return result


def _trace_matches_search(
    trace: CommandTrace,
    *,
    query: str,
    support_status: str,
    command_family: str,
    scenario_id: str,
) -> bool:
    if support_status and trace.support_status != support_status:
        return False
    if command_family and trace.command_family != command_family:
        return False
    if scenario_id and scenario_id not in trace.active_scenarios:
        return False
    if not query:
        return True
    haystack = "\n".join([
        trace.raw_input,
        trace.stdout,
        trace.stderr,
        trace.fingerprint,
        trace.guessed_intent,
        trace.matched_rule_id,
    ]).lower()
    return query.lower() in haystack


@dataclass
class WorkloadMutation:
    replicas: int | None = None
    deployment_status: str = ""
    pod_status: str = ""
    ready_replicas: int | None = None
    restarts_delta: int = 0
    deleted: bool = False
    updated_at: str = ""


@dataclass
class HelmReleaseMutation:
    revisions: list[dict[str, Any]] | None = None
    uninstalled: bool = False
    values: dict[str, str] = field(default_factory=dict)
    updated_at: str = ""


@dataclass
class SimulationMutations:
    """Thread-safe in-memory overlay for mutating simulator operations."""

    workloads: dict[str, WorkloadMutation] = field(default_factory=dict)
    deleted_pods: set[str] = field(default_factory=set)
    deleted_resources: dict[str, set[str]] = field(default_factory=dict)
    created_resources: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    extra_events: list[dict[str, str]] = field(default_factory=list)
    extra_event_limit: int = DEFAULT_TRACE_LIMIT
    release: HelmReleaseMutation = field(default_factory=HelmReleaseMutation)
    version: int = 0
    lock: threading.RLock = field(default_factory=threading.RLock)

    def summary(self) -> dict[str, Any]:
        with self.lock:
            return {
                "version": self.version,
                "workloads": {
                    name: {
                        "replicas": mutation.replicas,
                        "deployment_status": mutation.deployment_status,
                        "pod_status": mutation.pod_status,
                        "ready_replicas": mutation.ready_replicas,
                        "restarts_delta": mutation.restarts_delta,
                        "deleted": mutation.deleted,
                        "updated_at": mutation.updated_at,
                    }
                    for name, mutation in sorted(self.workloads.items())
                },
                "deleted_pods": sorted(self.deleted_pods),
                "deleted_resources": {
                    kind: sorted(names)
                    for kind, names in sorted(self.deleted_resources.items())
                    if names
                },
                "created_resources": {
                    kind: sorted(items)
                    for kind, items in sorted(self.created_resources.items())
                    if items
                },
                "extra_event_count": len(self.extra_events),
                "extra_event_limit": self.extra_event_limit,
                "release": {
                    "uninstalled": self.release.uninstalled,
                    "revision_count": len(self.release.revisions or []),
                    "values": dict(sorted(self.release.values.items())),
                    "updated_at": self.release.updated_at,
                },
            }

    def record_event(self, event_type: str, reason: str, obj: str, message: str, now: _dt.datetime) -> None:
        with self.lock:
            self.extra_events.append({
                "last_seen": _format_dt(now),
                "type": event_type,
                "reason": reason,
                "object": obj,
                "message": message,
            })
            limit = max(self.extra_event_limit, 0)
            if limit:
                overflow = len(self.extra_events) - limit
                if overflow > 0:
                    del self.extra_events[:overflow]
            else:
                self.extra_events.clear()
            self.version += 1

    def set_workload(
        self,
        component: str,
        *,
        now: _dt.datetime,
        replicas: int | None = None,
        deployment_status: str = "",
        pod_status: str = "",
        ready_replicas: int | None = None,
        restarts_delta: int = 0,
        deleted: bool | None = None,
    ) -> WorkloadMutation:
        with self.lock:
            mutation = self.workloads.setdefault(component, WorkloadMutation())
            if replicas is not None:
                mutation.replicas = max(0, replicas)
            if deployment_status:
                mutation.deployment_status = deployment_status
            if pod_status:
                mutation.pod_status = pod_status
            if ready_replicas is not None:
                mutation.ready_replicas = max(0, ready_replicas)
            if restarts_delta:
                mutation.restarts_delta += restarts_delta
            if deleted is not None:
                mutation.deleted = deleted
            mutation.updated_at = _format_dt(now)
            self.version += 1
            return mutation

    def delete_pod(self, pod_name: str, *, now: _dt.datetime) -> None:
        component = _component_from_pod_name(pod_name)
        with self.lock:
            self.deleted_pods.add(pod_name)
        self.set_workload(
            component,
            now=now,
            deployment_status="Restarting",
            pod_status="Running",
            restarts_delta=1,
        )
        self.record_event(
            "Normal",
            "Killing",
            f"pod/{pod_name}",
            f"pod {pod_name} deleted; controller recreated replacement pod",
            now,
        )

    def put_resource(self, kind: str, name: str, row: dict[str, Any], *, now: _dt.datetime) -> None:
        with self.lock:
            self.created_resources.setdefault(kind, {})[name] = dict(row)
            self.deleted_resources.setdefault(kind, set()).discard(name)
            self.version += 1
        self.record_event(
            "Normal",
            "Configured",
            f"{_resource_prefix(kind)}/{name}",
            f"{_resource_prefix(kind)} {name} configured in simulator state",
            now,
        )

    def delete_resource(self, kind: str, name: str, *, now: _dt.datetime) -> None:
        with self.lock:
            self.created_resources.setdefault(kind, {}).pop(name, None)
            self.deleted_resources.setdefault(kind, set()).add(name)
            self.version += 1
        self.record_event(
            "Normal",
            "Deleted",
            f"{_resource_prefix(kind)}/{name}",
            f"{_resource_prefix(kind)} {name} deleted from simulator state",
            now,
        )

    def reset(self) -> None:
        with self.lock:
            self.workloads.clear()
            self.deleted_pods.clear()
            self.deleted_resources.clear()
            self.created_resources.clear()
            self.extra_events.clear()
            self.release = HelmReleaseMutation()
            self.version += 1

    def current_revisions(self, base: list[dict[str, Any]]) -> list[dict[str, Any]]:
        with self.lock:
            if self.release.revisions is None:
                return [dict(item) for item in base]
            return [dict(item) for item in self.release.revisions]

    def set_revisions(self, revisions: list[dict[str, Any]], *, now: _dt.datetime, uninstalled: bool = False) -> None:
        with self.lock:
            self.release.revisions = [dict(item) for item in revisions]
            self.release.uninstalled = uninstalled
            if uninstalled:
                self.release.values.clear()
            self.release.updated_at = _format_dt(now)
            self.version += 1

    def set_release_values(self, values: dict[str, str], *, now: _dt.datetime) -> None:
        with self.lock:
            self.release.values.update(values)
            self.release.updated_at = _format_dt(now)
            self.version += 1


@dataclass
class ContinuousGenerationStatus:
    enabled: bool = False
    interval_seconds: float = 0.0
    thread: str = "disabled"
    generation_count: int = 0
    last_started_at: str = ""
    last_completed_at: str = ""
    last_error: str = ""
    last_anomaly_count: int = 0
    last_seed: int | None = None
    lock: threading.RLock = field(default_factory=threading.RLock)

    def to_dict(self) -> dict[str, Any]:
        with self.lock:
            return {
                "enabled": self.enabled,
                "interval_seconds": self.interval_seconds,
                "thread": self.thread,
                "generation_count": self.generation_count,
                "last_started_at": self.last_started_at,
                "last_completed_at": self.last_completed_at,
                "last_error": self.last_error,
                "last_anomaly_count": self.last_anomaly_count,
                "last_seed": self.last_seed,
            }


@dataclass
class SimulationState:
    legacy: Any
    args: Any
    output_dir: Path
    namespace: str
    active_scenarios: tuple[str, ...]
    components: tuple[str, ...]
    anomaly_rows: list[dict[str, str]]
    clock: SimulationClock
    traces: CommandTraceStore
    mutations: SimulationMutations = field(default_factory=SimulationMutations)
    generation: ContinuousGenerationStatus = field(default_factory=ContinuousGenerationStatus)
    otel_status: dict[str, Any] = field(default_factory=dict)

    def profiles(self) -> list[OpsScenarioProfile]:
        profiles: list[OpsScenarioProfile] = []
        for scenario_id in self.active_scenarios:
            profile = OPS_SCENARIO_PROFILES.get(scenario_id)
            if profile is not None:
                profiles.append(profile)
        return profiles

    def summary(self) -> dict[str, Any]:
        return {
            "namespace": self.namespace,
            "output_dir": str(self.output_dir),
            "clock": self.clock.to_dict(),
            "active_scenarios": list(self.active_scenarios),
            "components": list(self.components),
            "anomaly_count": self.generated_row_count(),
            "command_trace_count": self.traces.count(),
            "unsupported_group_count": len(self.traces.unsupported_summary()),
            "otel": self.otel_status,
            "generation": self.generation.to_dict(),
            "mutations": self.mutations.summary(),
            "profiles": [
                {
                    "scenario_id": profile.scenario_id,
                    "summary": profile.summary,
                    "affected_components": list(profile.affected_components),
                }
                for profile in self.profiles()
            ],
            "active_anomalies": self.active_anomalies(limit=20),
        }

    def active_anomalies(self, limit: int = 50) -> list[dict[str, str]]:
        now = self.clock.now()
        matches: list[dict[str, str]] = []
        for row in self._generated_rows_reference():
            start = _parse_optional_timestamp(row.get("span_start") or row.get("timestamp"))
            end = _parse_optional_timestamp(row.get("span_end") or row.get("timestamp"))
            if start is None or end is None:
                continue
            if start <= now <= end:
                matches.append(row)
                if len(matches) >= limit:
                    break
        return matches

    def generated_rows(self) -> list[dict[str, str]]:
        with self.generation.lock:
            return list(self.anomaly_rows)

    def generated_rows_slice(self, limit: int) -> list[dict[str, str]]:
        with self.generation.lock:
            return list(self.anomaly_rows[:max(limit, 0)])

    def _generated_rows_reference(self) -> list[dict[str, str]]:
        with self.generation.lock:
            return self.anomaly_rows

    def generated_row_count(self) -> int:
        with self.generation.lock:
            return len(self.anomaly_rows)

    def replace_generated_rows(self, rows: list[dict[str, str]]) -> None:
        with self.generation.lock:
            self.anomaly_rows = rows


def build_state(
    legacy_module: Any,
    args: Any,
    *,
    namespace: str = DEFAULT_NAMESPACE,
    trace_limit: int = DEFAULT_TRACE_LIMIT,
    persist_command_log: Path | None = None,
    persist_command_db: Path | None = None,
) -> SimulationState:
    validate_ops_profiles(legacy_module)
    active_scenarios = tuple(sorted(legacy_module._resolve_scenarios(args)))
    components = tuple(name for name in legacy_module.COMPONENTS if name in args.components)
    anomaly_rows = load_anomaly_rows(args.output_dir / "anomalies.csv")
    clock = SimulationClock(
        start_time=legacy_module.START,
        speedup=float(getattr(args, "otel_stream_speedup", 3600.0)),
    )
    return SimulationState(
        legacy=legacy_module,
        args=args,
        output_dir=args.output_dir,
        namespace=namespace,
        active_scenarios=active_scenarios,
        components=components,
        anomaly_rows=anomaly_rows,
        clock=clock,
        traces=CommandTraceStore(
            limit=trace_limit,
            persist_path=persist_command_log,
            sqlite_path=persist_command_db,
        ),
        mutations=SimulationMutations(extra_event_limit=trace_limit),
        otel_status={
            "enabled": bool(getattr(args, "otel_enabled", False)),
            "signals": sorted(getattr(args, "otel_signal_selection", None) or []),
            "gauges": bool(getattr(args, "otel_emit_gauges", False)),
            "thread": "not_started",
        },
    )


def load_anomaly_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


_VALUE_FLAGS = {
    "-n", "--namespace", "-o", "--output", "-l", "--selector",
    "--context", "--kubeconfig", "-c", "--container", "--tail", "--since",
    "--field-selector", "--sort-by", "--for", "--timeout", "--replicas",
    "-f", "--filename", "--from-literal", "--from-file", "--image", "--schedule",
    "--set", "--set-string", "--values",
}
_BOOL_FLAGS = {
    "-A", "--all-namespaces", "--previous", "-p", "--follow",
    "--watch", "-w", "--wide", "--show-labels", "--dry-run", "--install",
    "--atomic", "--debug", "--all", "--short", "--",
}
_SENSITIVE_FLAG_TOKENS = ("token", "password", "secret", "client-key")
_SENSITIVE_QUERY_KEYS = {
    "access_token",
    "authorization",
    "client_secret",
    "password",
    "secret",
    "token",
}
_MODELED_FLAGS = {
    "namespace",
    "-A", "--all-namespaces",
    "-o", "--output",
    "-l", "--selector",
    "-c", "--container",
    "--tail", "--since", "--follow",
    "--previous", "-p",
    "--wide", "--show-labels",
    "--field-selector", "--sort-by", "--for", "--timeout",
    "--replicas", "-f", "--filename", "--from-literal", "--from-file",
    "--image", "--schedule", "--set", "--set-string", "--values",
    "--dry-run", "--install", "--atomic", "--debug", "--all", "--short", "--",
}
_KIND_ALIASES = {
    "all": "all",
    "ns": "namespaces",
    "namespace": "namespaces",
    "namespaces": "namespaces",
    "po": "pods",
    "pod": "pods",
    "pods": "pods",
    "cm": "configmaps",
    "configmap": "configmaps",
    "configmaps": "configmaps",
    "secret": "secrets",
    "secrets": "secrets",
    "rc": "replicationcontrollers",
    "replicationcontroller": "replicationcontrollers",
    "replicationcontrollers": "replicationcontrollers",
    "deploy": "deployments",
    "deployment": "deployments",
    "deployments": "deployments",
    "rs": "replicasets",
    "replicaset": "replicasets",
    "replicasets": "replicasets",
    "ds": "daemonsets",
    "daemonset": "daemonsets",
    "daemonsets": "daemonsets",
    "svc": "services",
    "service": "services",
    "services": "services",
    "ep": "endpoints",
    "endpoint": "endpoints",
    "endpoints": "endpoints",
    "endpointslice": "endpointslices",
    "endpointslices": "endpointslices",
    "events": "events",
    "event": "events",
    "hpa": "hpa",
    "horizontalpodautoscaler": "hpa",
    "horizontalpodautoscalers": "hpa",
    "job": "jobs",
    "jobs": "jobs",
    "cj": "cronjobs",
    "cronjob": "cronjobs",
    "cronjobs": "cronjobs",
    "sa": "serviceaccounts",
    "serviceaccount": "serviceaccounts",
    "serviceaccounts": "serviceaccounts",
    "node": "nodes",
    "nodes": "nodes",
    "no": "nodes",
    "pvc": "pvc",
    "pvcs": "pvc",
    "persistentvolumeclaim": "pvc",
    "persistentvolumeclaims": "pvc",
    "sts": "statefulsets",
    "statefulset": "statefulsets",
    "statefulsets": "statefulsets",
    "ing": "ingress",
    "ingress": "ingress",
    "ingresses": "ingress",
}
_SNAPSHOT_KINDS = {
    "namespaces",
    "pods",
    "configmaps",
    "secrets",
    "replicationcontrollers",
    "deployments",
    "replicasets",
    "daemonsets",
    "services",
    "endpoints",
    "endpointslices",
    "events",
    "hpa",
    "jobs",
    "cronjobs",
    "serviceaccounts",
    "nodes",
    "pvc",
    "statefulsets",
    "ingress",
}
_MUTATION_SNAPSHOT_KINDS = {
    "configmaps",
    "secrets",
    "deployments",
    "daemonsets",
    "services",
    "hpa",
    "jobs",
    "cronjobs",
    "serviceaccounts",
    "pvc",
    "statefulsets",
    "ingress",
}


def run_command(
    state: SimulationState,
    *,
    command: str | None = None,
    argv: list[str] | tuple[str, ...] | None = None,
    client: str = "api",
) -> dict[str, Any]:
    started = time.perf_counter()
    received = _dt.datetime.now(_dt.timezone.utc).isoformat()
    parsed = parse_command(command=command, argv=argv, default_namespace=state.namespace)
    simulated_time = _format_dt(state.clock.now())
    result = render_command(state, parsed)
    latency_ms = (time.perf_counter() - started) * 1000.0
    fingerprint = command_fingerprint(parsed, result.support_status)
    redacted_raw_input = _redact_command_for_trace(parsed)
    trace = CommandTrace(
        id=state.traces.next_id(),
        received_at_wall_time=received,
        simulated_time=simulated_time,
        raw_input=redacted_raw_input,
        argv=_redact_argv(parsed.argv),
        client=client,
        command_family=parsed.family,
        verb=parsed.verb,
        resource_kind=parsed.resource_kind,
        resource_name=parsed.resource_name,
        namespace=parsed.namespace,
        parsed_flags=_redact_parsed_flags(parsed.flags),
        support_status=result.support_status,
        matched_rule_id=result.matched_rule_id,
        active_scenarios=state.active_scenarios,
        exit_code=result.exit_code,
        stdout_preview=_preview(result.stdout),
        stderr_preview=_preview(result.stderr),
        stdout=result.stdout,
        stderr=result.stderr,
        latency_ms=round(latency_ms, 3),
        fingerprint=fingerprint,
        guessed_intent=guess_intent(parsed),
    )
    state.traces.record(trace)
    return {
        "trace": trace.to_dict(),
        "result": {
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "support_status": result.support_status,
            "matched_rule_id": result.matched_rule_id,
        },
    }


def parse_command(
    *,
    command: str | None = None,
    argv: list[str] | tuple[str, ...] | None = None,
    default_namespace: str = DEFAULT_NAMESPACE,
) -> ParsedCommand:
    if argv is None:
        raw = command or ""
        try:
            argv_tuple = tuple(shlex.split(raw))
        except ValueError as exc:
            return ParsedCommand(
                raw_input=raw,
                argv=(),
                family="unknown",
                verb="",
                resource_kind="",
                resource_name="",
                namespace=default_namespace,
                flags={},
                positionals=(),
                parse_error=str(exc),
            )
    else:
        argv_tuple = tuple(str(item) for item in argv)
        raw = command if command is not None else shlex.join(argv_tuple)
    if not argv_tuple:
        return ParsedCommand(raw, argv_tuple, "unknown", "", "", "", default_namespace, {}, ())

    family_token = Path(argv_tuple[0]).name
    family = "kubectl" if family_token in {"kubectl", "k"} else family_token
    namespace, flags, positionals = _split_flags(argv_tuple[1:], default_namespace)
    if family == "kubectl":
        return _parse_kubectl(raw, argv_tuple, namespace, flags, positionals)
    if family == "helm":
        return _parse_helm(raw, argv_tuple, namespace, flags, positionals)
    return ParsedCommand(raw, argv_tuple, family, "", "", "", namespace, flags, tuple(positionals))


def _split_flags(tokens: tuple[str, ...], default_namespace: str) -> tuple[str, dict[str, Any], list[str]]:
    namespace = default_namespace
    flags: dict[str, Any] = {}
    positionals: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in {"-n", "--namespace"}:
            value = tokens[i + 1] if i + 1 < len(tokens) else ""
            namespace = value or namespace
            flags["namespace"] = value
            i += 2
            continue
        if token.startswith("--namespace="):
            value = token.split("=", 1)[1]
            namespace = value or namespace
            flags["namespace"] = value
            i += 1
            continue
        if token in {"-A", "--all-namespaces"}:
            namespace = "*"
            flags[token] = True
            i += 1
            continue
        if token in _VALUE_FLAGS:
            flags[token] = tokens[i + 1] if i + 1 < len(tokens) else ""
            i += 2
            continue
        if any(token.startswith(prefix + "=") for prefix in _VALUE_FLAGS if prefix.startswith("--")):
            key, value = token.split("=", 1)
            flags[key] = value
            i += 1
            continue
        if token in _BOOL_FLAGS:
            flags[token] = True
            i += 1
            continue
        if token.startswith("-"):
            flags[token] = True
            i += 1
            continue
        positionals.append(token)
        i += 1
    return namespace, flags, positionals


def _parse_kubectl(
    raw: str,
    argv: tuple[str, ...],
    namespace: str,
    flags: dict[str, Any],
    positionals: list[str],
) -> ParsedCommand:
    verb = positionals[0] if positionals else ""
    resource_kind = ""
    resource_name = ""
    if verb in {"api-resources", "api-versions", "cluster-info", "version"}:
        return ParsedCommand(raw, argv, "kubectl", verb, "", "", namespace, flags, tuple(positionals))
    if verb == "events":
        return ParsedCommand(raw, argv, "kubectl", "get", "events", "", namespace, flags, tuple(positionals))
    if verb == "logs":
        target = positionals[1] if len(positionals) > 1 else ""
        follow_value = flags.get("-f")
        if "-f" in flags:
            flags["--follow"] = True
            if not target and isinstance(follow_value, str) and follow_value:
                target = follow_value
        resource_kind, resource_name = _split_resource_token(target, default_kind="pods")
        if not resource_name:
            resource_kind, resource_name = "pods", target
        return ParsedCommand(
            raw, argv, "kubectl", verb,
            resource_kind, resource_name, namespace, flags, tuple(positionals)
        )
    if verb == "auth":
        subverb = positionals[1] if len(positionals) > 1 else ""
        resource_kind = _normalize_kind(positionals[3]) if len(positionals) > 3 else ""
        return ParsedCommand(
            raw, argv, "kubectl", f"auth {subverb}".strip(),
            resource_kind, "", namespace, flags, tuple(positionals)
        )
    if verb == "config":
        subverb = positionals[1] if len(positionals) > 1 else ""
        return ParsedCommand(
            raw, argv, "kubectl", f"config {subverb}".strip(),
            "config", "", namespace, flags, tuple(positionals)
        )
    if verb == "rollout":
        subverb = positionals[1] if len(positionals) > 1 else ""
        target = positionals[2] if len(positionals) > 2 else ""
        resource_kind, resource_name = _split_resource_token(target)
        return ParsedCommand(
            raw, argv, "kubectl", f"rollout {subverb}".strip(),
            resource_kind, resource_name, namespace, flags, tuple(positionals)
        )
    if verb in {"delete", "scale"}:
        target = positionals[1] if len(positionals) > 1 else ""
        resource_kind, resource_name = _split_resource_token(target)
        if not resource_name and len(positionals) > 2:
            resource_name = positionals[2]
        return ParsedCommand(
            raw, argv, "kubectl", verb,
            resource_kind, resource_name, namespace, flags, tuple(positionals)
        )
    if verb == "create":
        resource_kind = _normalize_kind(positionals[1]) if len(positionals) > 1 else ""
        resource_name = positionals[2] if len(positionals) > 2 else ""
        return ParsedCommand(
            raw, argv, "kubectl", verb, resource_kind, resource_name,
            namespace, flags, tuple(positionals)
        )
    if verb == "apply":
        return ParsedCommand(
            raw, argv, "kubectl", verb, "manifest", "", namespace, flags, tuple(positionals)
        )
    if verb == "wait":
        target = positionals[1] if len(positionals) > 1 else ""
        resource_kind, resource_name = _split_resource_token(target)
        return ParsedCommand(
            raw, argv, "kubectl", "wait",
            resource_kind, resource_name, namespace, flags, tuple(positionals)
        )
    if verb in {"exec", "port-forward"}:
        target = positionals[1] if len(positionals) > 1 else ""
        resource_kind, resource_name = _split_resource_token(target, default_kind="pods")
        if not resource_name:
            resource_kind, resource_name = "pods", target
        return ParsedCommand(
            raw, argv, "kubectl", verb,
            resource_kind, resource_name, namespace, flags, tuple(positionals)
        )
    if len(positionals) > 1:
        resource_kind, resource_name = _split_resource_token(positionals[1])
        if not resource_name and len(positionals) > 2:
            resource_name = positionals[2]
    return ParsedCommand(
        raw, argv, "kubectl", verb, resource_kind, resource_name,
        namespace, flags, tuple(positionals)
    )


def _parse_helm(
    raw: str,
    argv: tuple[str, ...],
    namespace: str,
    flags: dict[str, Any],
    positionals: list[str],
) -> ParsedCommand:
    verb = positionals[0] if positionals else ""
    resource_kind = "release"
    resource_name = ""
    if verb in {"version", "env", "template"}:
        resource_kind = verb
        resource_name = positionals[1] if len(positionals) > 1 else ""
        return ParsedCommand(
            raw, argv, "helm", verb, resource_kind, resource_name,
            namespace, flags, tuple(positionals)
        )
    if verb == "get":
        resource_kind = positionals[1] if len(positionals) > 1 else ""
        resource_name = positionals[2] if len(positionals) > 2 else ""
    elif verb in {"test", "upgrade", "rollback", "uninstall", "install"}:
        resource_kind = verb
        resource_name = positionals[1] if len(positionals) > 1 else ""
    elif len(positionals) > 1:
        resource_name = positionals[1]
    return ParsedCommand(
        raw, argv, "helm", verb, resource_kind, resource_name,
        namespace, flags, tuple(positionals)
    )


def _split_resource_token(token: str, default_kind: str = "") -> tuple[str, str]:
    if not token:
        return default_kind, ""
    if "/" in token:
        raw_kind, name = token.split("/", 1)
    else:
        raw_kind, name = token, ""
    return _normalize_kind(raw_kind or default_kind), name


def _normalize_kind(raw: str) -> str:
    return _KIND_ALIASES.get(raw.lower(), raw.lower())


def render_command(state: SimulationState, parsed: ParsedCommand) -> CommandResult:
    if parsed.parse_error:
        return CommandResult(2, "", parsed.parse_error + "\n", "unsupported", "parse.error")
    if parsed.family == "kubectl":
        return _with_flag_support(parsed, _render_kubectl(state, parsed))
    if parsed.family == "helm":
        return _with_flag_support(parsed, _render_helm(state, parsed))
    return CommandResult(
        127,
        "",
        f"{parsed.family or 'command'}: command not supported by simulator\n",
        "unsupported",
        "family.unsupported",
    )


def _with_flag_support(parsed: ParsedCommand, result: CommandResult) -> CommandResult:
    if result.support_status != "supported":
        return result
    unmodeled = sorted(flag for flag in parsed.flags if flag not in _MODELED_FLAGS)
    if not unmodeled:
        return result
    warning = "warning: flag(s) parsed but not modeled: " + ", ".join(unmodeled) + "\n"
    return CommandResult(
        result.exit_code,
        result.stdout,
        result.stderr + warning,
        "partial",
        result.matched_rule_id + ".partial-flags",
    )


def _render_kubectl(state: SimulationState, parsed: ParsedCommand) -> CommandResult:
    kind = parsed.resource_kind
    if parsed.verb == "version":
        return CommandResult(0, _render_kubectl_version(), "", "supported", "kubectl.version")
    if parsed.verb == "api-versions":
        return CommandResult(0, _render_kubectl_api_versions(), "", "supported", "kubectl.api-versions")
    if parsed.verb == "api-resources":
        return CommandResult(0, _render_kubectl_api_resources(), "", "supported", "kubectl.api-resources")
    if parsed.verb == "cluster-info":
        return CommandResult(0, _render_kubectl_cluster_info(), "", "supported", "kubectl.cluster-info")
    if parsed.verb == "config current-context":
        return CommandResult(0, "amc-simulator\n", "", "supported", "kubectl.config.current-context")
    if parsed.verb == "config view":
        return CommandResult(
            0,
            render_kubeconfig("http://127.0.0.1:8088", state.namespace),
            "",
            "supported",
            "kubectl.config.view",
        )
    if parsed.verb == "auth can-i":
        return CommandResult(0, "yes\n", "", "supported", "kubectl.auth.can-i")
    if parsed.verb == "get":
        if kind in _SNAPSHOT_KINDS or kind == "all":
            return CommandResult(
                0, _render_get(state, kind, parsed), "", "supported", f"kubectl.get.{kind}"
            )
        return _unsupported(parsed, f"kubectl get {kind or '<missing-kind>'}")
    if parsed.verb == "describe":
        if kind in _SNAPSHOT_KINDS:
            return _render_describe(state, kind, parsed)
        return _unsupported(parsed, f"kubectl describe {kind or '<missing-kind>'}")
    if parsed.verb == "logs":
        if parsed.resource_name:
            return CommandResult(0, _render_logs(state, parsed), "", "supported", "kubectl.logs.pod")
        return CommandResult(1, "", "error: expected pod name for logs\n", "partial", "kubectl.logs.missing-pod")
    if parsed.verb == "top":
        if kind in {"pods", "nodes"}:
            return CommandResult(0, _render_top(state, kind), "", "supported", f"kubectl.top.{kind}")
        return _unsupported(parsed, f"kubectl top {kind or '<missing-kind>'}")
    if parsed.verb == "rollout status":
        if kind in {"deployments", "deployment", "deploy"} or parsed.resource_name:
            return CommandResult(
                0, _render_rollout_status(state, parsed), "", "supported", "kubectl.rollout.status"
            )
        return _unsupported(parsed, "kubectl rollout status")
    if parsed.verb == "rollout history":
        if kind in {"deployments", "deployment", "deploy"} or parsed.resource_name:
            return CommandResult(
                0, _render_rollout_history(state, parsed), "", "supported", "kubectl.rollout.history"
            )
        return _unsupported(parsed, "kubectl rollout history")
    if parsed.verb == "rollout restart":
        if kind in {"deployments", "deployment", "deploy"} or parsed.resource_name:
            return CommandResult(
                0, _render_rollout_restart(state, parsed), "", "supported", "kubectl.rollout.restart"
            )
        return _unsupported(parsed, "kubectl rollout restart")
    if parsed.verb == "scale":
        return CommandResult(0, _render_scale(state, parsed), "", "supported", "kubectl.scale")
    if parsed.verb == "delete":
        return CommandResult(0, _render_delete(state, parsed), "", "supported", "kubectl.delete")
    if parsed.verb in {"apply", "create"}:
        return CommandResult(0, _render_apply(state, parsed), "", "supported", f"kubectl.{parsed.verb}")
    if parsed.verb == "wait":
        return CommandResult(0, _render_wait(state, parsed), "", "supported", "kubectl.wait")
    if parsed.verb == "exec":
        return CommandResult(0, _render_exec(state, parsed), "", "supported", "kubectl.exec")
    if parsed.verb == "port-forward":
        return CommandResult(
            0, _render_port_forward(parsed), "", "supported", "kubectl.port-forward"
        )
    return _unsupported(parsed, f"kubectl {parsed.verb or '<missing-verb>'}")


def _render_helm(state: SimulationState, parsed: ParsedCommand) -> CommandResult:
    if parsed.verb == "version":
        return CommandResult(0, "version.BuildInfo{Version:\"v4.2.2\", GitCommit:\"simulated\"}\n", "", "supported", "helm.version")
    if parsed.verb == "env":
        return CommandResult(0, _render_helm_env(), "", "supported", "helm.env")
    if parsed.verb == "template":
        return CommandResult(0, _render_helm_get(state, "manifest"), "", "supported", "helm.template")
    if parsed.verb == "list":
        return CommandResult(0, _render_helm_list(state), "", "supported", "helm.list")
    if parsed.verb == "status":
        return CommandResult(0, _render_helm_status(state), "", "supported", "helm.status")
    if parsed.verb == "history":
        return CommandResult(0, _render_helm_history(state), "", "supported", "helm.history")
    if parsed.verb == "get":
        if parsed.resource_kind in {"values", "manifest", "notes", "all", "hooks"}:
            return CommandResult(
                0, _render_helm_get(state, parsed.resource_kind), "", "supported",
                f"helm.get.{parsed.resource_kind}",
            )
        return _unsupported(parsed, f"helm get {parsed.resource_kind or '<missing-kind>'}")
    if parsed.verb == "test":
        return CommandResult(0, _render_helm_test(state), "", "supported", "helm.test")
    if parsed.verb == "install":
        return CommandResult(0, _render_helm_install(state, parsed), "", "supported", "helm.install")
    if parsed.verb == "upgrade":
        return CommandResult(0, _render_helm_upgrade(state, parsed), "", "supported", "helm.upgrade")
    if parsed.verb == "rollback":
        return CommandResult(0, _render_helm_rollback(state, parsed), "", "supported", "helm.rollback")
    if parsed.verb == "uninstall":
        release = parsed.resource_name or DEFAULT_RELEASE
        now = state.clock.now()
        revisions = [
            {**revision, "status": "uninstalled" if revision["status"] == "deployed" else revision["status"]}
            for revision in _helm_release_revisions(state)
        ]
        state.mutations.set_revisions(revisions, now=now, uninstalled=True)
        state.mutations.record_event(
            "Normal",
            "HelmUninstall",
            f"release/{release}",
            f"release {release} uninstalled from simulator state",
            now,
        )
        return CommandResult(
            0,
            f"release \"{release}\" uninstalled\n",
            "",
            "supported",
            "helm.uninstall",
        )
    return _unsupported(parsed, f"helm {parsed.verb or '<missing-verb>'}")


def _unsupported(parsed: ParsedCommand, label: str) -> CommandResult:
    return CommandResult(
        1,
        "",
        f"{label} is not implemented by the simulator yet\n",
        "unsupported",
        "unsupported",
    )


def resource_snapshot(state: SimulationState) -> dict[str, list[dict[str, Any]]]:
    pods: list[dict[str, Any]] = []
    deployments: list[dict[str, Any]] = []
    replicasets: list[dict[str, Any]] = []
    services: list[dict[str, Any]] = []
    endpoints: list[dict[str, Any]] = []
    endpointslices: list[dict[str, Any]] = []
    hpas: list[dict[str, Any]] = []
    pvcs: list[dict[str, Any]] = []
    statefulsets: list[dict[str, Any]] = []
    daemonsets: list[dict[str, Any]] = []
    jobs: list[dict[str, Any]] = []
    cronjobs: list[dict[str, Any]] = []
    configmaps: list[dict[str, Any]] = []
    serviceaccounts: list[dict[str, Any]] = []
    ingress: list[dict[str, Any]] = []
    nodes = _node_rows(state)

    serviceaccounts.extend([
        {"name": "default", "secrets": 0, "age": "30d"},
        {"name": DEFAULT_RELEASE, "secrets": 0, "age": "7d"},
    ])
    configmaps.extend([
        {
            "name": "simulated-saas-config",
            "data": 4,
            "age": "7d",
            "keys": {
                "LOG_LEVEL": "info",
                "FEATURE_FLAGS": "checkout_v2,adaptive_cache",
                "OTEL_EXPORTER": "enabled",
                "SCENARIOS": ",".join(state.active_scenarios),
            },
        },
        {
            "name": "simulated-saas-runbook",
            "data": 2,
            "age": "7d",
            "keys": {
                "summary": "Synthetic incident runbook for AMC server mode",
                "first_steps": "kubectl get pods; kubectl get events; helm status simulated-saas",
            },
        },
    ])

    with state.mutations.lock:
        deleted_components = {
            name for name, mutation in state.mutations.workloads.items()
            if mutation.deleted
        }
        deleted_pods = set(state.mutations.deleted_pods)

    for component in state.components:
        if component in deleted_components:
            continue
        health = _component_health(state, component)
        replicas = _replica_count(state, component)
        ready_replicas = min(replicas, health["ready_replicas"])
        deployments.append({
            "name": component,
            "ready": f"{ready_replicas}/{replicas}",
            "up_to_date": ready_replicas,
            "available": ready_replicas,
            "age": "7d",
            "status": health["deployment_status"],
        })
        replicasets.append({
            "name": f"{component}-6d9f7c8b9d",
            "desired": replicas,
            "current": replicas,
            "ready": ready_replicas,
            "age": "7d",
            "owner": component,
        })
        services.append({
            "name": component,
            "type": "ClusterIP",
            "cluster_ip": _stable_cluster_ip(component),
            "external_ip": "<none>",
            "ports": "8080/TCP",
            "age": "7d",
        })
        endpoints.append({
            "name": component,
            "endpoints": "",
            "ports": "8080",
            "age": "7d",
        })
        endpointslices.append({
            "name": f"{component}-slice",
            "address_type": "IPv4",
            "ports": "8080",
            "endpoints": 0,
            "age": "7d",
            "service": component,
        })
        hpas.append({
            "name": component,
            "reference": f"Deployment/{component}",
            "targets": f"{health['cpu_pct']}%/80%",
            "minpods": 1,
            "maxpods": 8,
            "replicas": replicas,
            "age": "7d",
        })
        endpoint_ips: list[str] = []
        for index in range(replicas):
            pod_name = _pod_name(component, index)
            if pod_name in deleted_pods:
                continue
            pod_ip = _stable_pod_ip(pod_name)
            endpoint_ips.append(pod_ip)
            pods.append({
                "name": pod_name,
                "component": component,
                "ready": health["ready"],
                "status": health["pod_status"],
                "restarts": health["restarts"] + (1 if index == 0 and health["pod_status"] != "Running" else 0),
                "age": "7d",
                "node": nodes[index % len(nodes)]["name"],
                "pod_ip": pod_ip,
                "cpu_m": health["cpu_m"],
                "memory_mi": health["memory_mi"],
                "scenario_ids": _component_scenarios(state, component),
                "events": _component_events(state, component),
            })
        endpoints[-1]["endpoints"] = ",".join(f"{ip}:8080" for ip in endpoint_ips[:3])
        endpointslices[-1]["endpoints"] = len(endpoint_ips)
        if component == "database":
            statefulsets.append({
                "name": "database",
                "ready": f"{ready_replicas}/{replicas}",
                "age": "7d",
            })
            pvcs.append({
                "name": "database-data-database-0",
                "status": "Bound",
                "volume": "pvc-database-0",
                "capacity": "200Gi",
                "access_modes": "RWO",
                "storageclass": "gp3",
                "age": "7d",
                "used_pct": health["pvc_used_pct"],
            })
    if "observabilitypipeline" in state.components:
        daemonsets.append({
            "name": "observability-agent",
            "desired": len(nodes),
            "current": len(nodes),
            "ready": len(nodes),
            "up_to_date": len(nodes),
            "available": len(nodes),
            "node_selector": "kubernetes.io/os=linux",
            "age": "7d",
        })
    else:
        daemonsets.append({
            "name": "node-observer",
            "desired": len(nodes),
            "current": len(nodes),
            "ready": len(nodes),
            "up_to_date": len(nodes),
            "available": len(nodes),
            "node_selector": "kubernetes.io/os=linux",
            "age": "7d",
        })
    jobs.append({
        "name": "scheduler-backfill",
        "completions": "1/1",
        "duration": "2m14s",
        "age": "6d",
    })
    cronjobs.append({
        "name": "scheduler-nightly",
        "schedule": "15 2 * * *",
        "suspend": "False",
        "active": 0,
        "last_schedule": "18h",
        "age": "7d",
    })
    if "apigateway" in state.components:
        ingress.append({
            "name": "apigateway",
            "class": "nginx",
            "hosts": "api.simulated-saas.local",
            "address": "10.0.0.20",
            "ports": "80,443",
            "age": "7d",
        })
    snapshot = {
        "namespaces": [{"name": state.namespace, "status": "Active", "age": "30d"}],
        "pods": pods,
        "configmaps": configmaps,
        "secrets": [
            {
                "name": f"sh.helm.release.v1.{DEFAULT_RELEASE}.v{revision['version']}",
                "type": "helm.sh/release.v1",
                "data": 1,
                "age": "7d",
            }
            for revision in _helm_release_revisions(state)
        ],
        "replicationcontrollers": [],
        "deployments": deployments,
        "replicasets": replicasets,
        "daemonsets": daemonsets,
        "services": services,
        "endpoints": endpoints,
        "endpointslices": endpointslices,
        "hpa": hpas,
        "nodes": nodes,
        "pvc": pvcs,
        "statefulsets": statefulsets,
        "jobs": jobs,
        "cronjobs": cronjobs,
        "serviceaccounts": serviceaccounts,
        "ingress": ingress,
        "events": _event_rows(state),
        "helm_releases": [_helm_release(state)],
    }
    _apply_mutation_rows(state, snapshot)
    return snapshot


def _apply_mutation_rows(state: SimulationState, snapshot: dict[str, list[dict[str, Any]]]) -> None:
    with state.mutations.lock:
        deleted = {
            kind: set(names)
            for kind, names in state.mutations.deleted_resources.items()
        }
        created = {
            kind: {name: dict(row) for name, row in rows.items()}
            for kind, rows in state.mutations.created_resources.items()
        }
    for kind, rows in snapshot.items():
        if kind in {"events", "helm_releases"}:
            continue
        deleted_names = deleted.get(kind, set())
        if deleted_names:
            snapshot[kind] = [row for row in rows if row.get("name") not in deleted_names]
        if kind in created:
            existing = {str(row.get("name")): index for index, row in enumerate(snapshot[kind])}
            for name, row in created[kind].items():
                if name in existing:
                    snapshot[kind][existing[name]] = row
                else:
                    snapshot[kind].append(row)


def _render_get(state: SimulationState, kind: str, parsed: ParsedCommand) -> str:
    resources = resource_snapshot(state)
    if kind == "all":
        return _render_get_all(state, parsed)
    rows = _filter_snapshot_rows(kind, resources.get(kind, []), parsed)
    if "-o" in parsed.flags or "--output" in parsed.flags:
        output = parsed.flags.get("-o", parsed.flags.get("--output"))
        if output == "json":
            return json.dumps({"items": rows}, indent=2) + "\n"
        if output == "name":
            return "".join(f"{_resource_prefix(kind)}/{row['name']}\n" for row in rows)
    if kind == "pods":
        if parsed.flags.get("-o") == "wide" or parsed.flags.get("--output") == "wide":
            return _table(["NAME", "READY", "STATUS", "RESTARTS", "AGE", "IP", "NODE"], [
                [r["name"], r["ready"], r["status"], str(r["restarts"]), r["age"], r["pod_ip"], r["node"]]
                for r in rows
            ])
        return _table(["NAME", "READY", "STATUS", "RESTARTS", "AGE"], [
            [r["name"], r["ready"], r["status"], str(r["restarts"]), r["age"]]
            for r in rows
        ])
    if kind == "namespaces":
        return _table(["NAME", "STATUS", "AGE"], [
            [r["name"], r["status"], r["age"]]
            for r in rows
        ])
    if kind == "configmaps":
        return _table(["NAME", "DATA", "AGE"], [
            [r["name"], str(r["data"]), r["age"]]
            for r in rows
        ])
    if kind == "secrets":
        return _table(["NAME", "TYPE", "DATA", "AGE"], [
            [r["name"], r["type"], str(r["data"]), r["age"]]
            for r in rows
        ])
    if kind == "replicationcontrollers":
        return _table(["NAME", "DESIRED", "CURRENT", "READY", "AGE"], [])
    if kind == "deployments":
        return _table(["NAME", "READY", "UP-TO-DATE", "AVAILABLE", "AGE"], [
            [r["name"], r["ready"], str(r["up_to_date"]), str(r["available"]), r["age"]]
            for r in rows
        ])
    if kind == "replicasets":
        return _table(["NAME", "DESIRED", "CURRENT", "READY", "AGE"], [
            [r["name"], str(r["desired"]), str(r["current"]), str(r["ready"]), r["age"]]
            for r in rows
        ])
    if kind == "daemonsets":
        return _table(["NAME", "DESIRED", "CURRENT", "READY", "UP-TO-DATE", "AVAILABLE", "NODE SELECTOR", "AGE"], [
            [
                r["name"], str(r["desired"]), str(r["current"]), str(r["ready"]),
                str(r["up_to_date"]), str(r["available"]), r["node_selector"], r["age"],
            ]
            for r in rows
        ])
    if kind == "services":
        return _table(["NAME", "TYPE", "CLUSTER-IP", "EXTERNAL-IP", "PORT(S)", "AGE"], [
            [r["name"], r["type"], r["cluster_ip"], r["external_ip"], r["ports"], r["age"]]
            for r in rows
        ])
    if kind == "endpoints":
        return _table(["NAME", "ENDPOINTS", "AGE"], [
            [r["name"], r["endpoints"] or "<none>", r["age"]]
            for r in rows
        ])
    if kind == "endpointslices":
        return _table(["NAME", "ADDRESSTYPE", "PORTS", "ENDPOINTS", "AGE"], [
            [r["name"], r["address_type"], r["ports"], str(r["endpoints"]), r["age"]]
            for r in rows
        ])
    if kind == "events":
        return _table(["LAST SEEN", "TYPE", "REASON", "OBJECT", "MESSAGE"], [
            [r["last_seen"], r["type"], r["reason"], r["object"], r["message"]]
            for r in rows
        ])
    if kind == "hpa":
        return _table(["NAME", "REFERENCE", "TARGETS", "MINPODS", "MAXPODS", "REPLICAS", "AGE"], [
            [r["name"], r["reference"], r["targets"], str(r["minpods"]), str(r["maxpods"]), str(r["replicas"]), r["age"]]
            for r in rows
        ])
    if kind == "jobs":
        return _table(["NAME", "COMPLETIONS", "DURATION", "AGE"], [
            [r["name"], r["completions"], r["duration"], r["age"]]
            for r in rows
        ])
    if kind == "cronjobs":
        return _table(["NAME", "SCHEDULE", "SUSPEND", "ACTIVE", "LAST SCHEDULE", "AGE"], [
            [r["name"], r["schedule"], r["suspend"], str(r["active"]), r["last_schedule"], r["age"]]
            for r in rows
        ])
    if kind == "serviceaccounts":
        return _table(["NAME", "SECRETS", "AGE"], [
            [r["name"], str(r["secrets"]), r["age"]]
            for r in rows
        ])
    if kind == "nodes":
        return _table(["NAME", "STATUS", "ROLES", "AGE", "VERSION"], [
            [r["name"], r["status"], r["roles"], r["age"], r["version"]]
            for r in rows
        ])
    if kind == "pvc":
        return _table(["NAME", "STATUS", "VOLUME", "CAPACITY", "ACCESS MODES", "STORAGECLASS", "AGE"], [
            [r["name"], r["status"], r["volume"], r["capacity"], r["access_modes"], r["storageclass"], r["age"]]
            for r in rows
        ])
    if kind == "statefulsets":
        return _table(["NAME", "READY", "AGE"], [
            [r["name"], r["ready"], r["age"]]
            for r in rows
        ])
    if kind == "ingress":
        return _table(["NAME", "CLASS", "HOSTS", "ADDRESS", "PORTS", "AGE"], [
            [r["name"], r["class"], r["hosts"], r["address"], r["ports"], r["age"]]
            for r in rows
        ])
    return ""


def _render_get_all(state: SimulationState, parsed: ParsedCommand) -> str:
    resources = resource_snapshot(state)
    rows = []
    for kind in ("pods", "services", "deployments", "replicasets", "statefulsets", "hpa", "jobs", "cronjobs"):
        for row in _filter_snapshot_rows(kind, resources.get(kind, []), parsed):
            status = (
                row.get("status")
                or row.get("ready")
                or row.get("targets")
                or row.get("completions")
                or row.get("schedule")
                or "Active"
            )
            rows.append([_resource_prefix(kind), row["name"], str(status), row.get("age", "7d")])
    if parsed.flags.get("-o") == "name" or parsed.flags.get("--output") == "name":
        return "".join(f"{kind}/{name}\n" for kind, name, _, _ in rows)
    return _table(["KIND", "NAME", "STATUS", "AGE"], rows)


def _filter_snapshot_rows(
    kind: str,
    rows: list[dict[str, Any]],
    parsed: ParsedCommand,
) -> list[dict[str, Any]]:
    label_selector = str(parsed.flags.get("-l") or parsed.flags.get("--selector") or "")
    field_selector = str(parsed.flags.get("--field-selector") or "")
    return [
        row for row in rows
        if _matches_label_selector(_snapshot_row_labels(kind, row), label_selector)
        and _snapshot_row_matches_field_selector(kind, row, field_selector)
    ]


def _snapshot_row_labels(kind: str, row: dict[str, Any]) -> dict[str, str]:
    component = row.get("component") or row.get("owner") or row.get("service") or row.get("name", "")
    labels = {
        "app.kubernetes.io/instance": DEFAULT_RELEASE,
        "app.kubernetes.io/name": str(component),
        "name": str(row.get("name", "")),
    }
    if kind == "secrets":
        labels.update({"owner": "helm", "name": DEFAULT_RELEASE})
    return labels


def _snapshot_row_matches_field_selector(kind: str, row: dict[str, Any], selector: str) -> bool:
    if not selector:
        return True
    fields = {
        "metadata.name": row.get("name", ""),
        "status.phase": "Running" if row.get("status") == "Running" else row.get("status", ""),
        "involvedObject.name": str(row.get("object", "")).split("/", 1)[-1],
        "kind": kind,
    }
    for item in _split_selector(selector):
        if "!=" in item:
            key, value = item.split("!=", 1)
            if str(fields.get(key.strip(), "")) == value.strip():
                return False
        elif "==" in item or "=" in item:
            separator = "==" if "==" in item else "="
            key, value = item.split(separator, 1)
            if str(fields.get(key.strip(), "")) != value.strip():
                return False
    return True


def _resource_prefix(kind: str) -> str:
    return {
        "namespaces": "namespace",
        "pods": "pod",
        "configmaps": "configmap",
        "secrets": "secret",
        "deployments": "deployment",
        "replicasets": "replicaset",
        "daemonsets": "daemonset",
        "services": "service",
        "endpoints": "endpoints",
        "endpointslices": "endpointslice",
        "events": "event",
        "hpa": "horizontalpodautoscaler",
        "jobs": "job",
        "cronjobs": "cronjob",
        "serviceaccounts": "serviceaccount",
        "nodes": "node",
        "pvc": "persistentvolumeclaim",
        "statefulsets": "statefulset",
        "ingress": "ingress",
    }.get(kind, kind.rstrip("s"))


def _normalized_resource_prefix(kind: str) -> str:
    normalized = _mutation_snapshot_kind(kind) or _normalize_kind(kind)
    return _resource_prefix(normalized or kind or "resource")


def _render_describe(state: SimulationState, kind: str, parsed: ParsedCommand) -> CommandResult:
    name = parsed.resource_name
    resources = resource_snapshot(state)
    if kind == "pods":
        pod = _find_named(resources["pods"], name)
        if pod is None:
            return _not_found("pods", name)
        lines = [
            f"Name:           {pod['name']}",
            f"Namespace:      {state.namespace}",
            f"Node:           {pod['node']}",
            f"Status:         {pod['status']}",
            f"Controlled By:  ReplicaSet/{pod['component']}",
            "Containers:",
            f"  {pod['component']}:",
            f"    Ready:      {pod['ready'].split('/')[0] == pod['ready'].split('/')[1]}",
            f"    Restarts:   {pod['restarts']}",
            "Events:",
        ]
        lines.extend("  " + event for event in pod["events"])
        return CommandResult(0, "\n".join(lines) + "\n", "", "supported", "kubectl.describe.pods")
    if kind == "deployments":
        deployment = _find_named(resources["deployments"], name)
        if deployment is None:
            return _not_found("deployments", name)
        component = deployment["name"]
        events = _component_events(state, component)
        lines = [
            f"Name:                   {component}",
            f"Namespace:              {state.namespace}",
            f"Replicas:               {deployment['ready']} available",
            f"DeploymentStatus:       {deployment['status']}",
            "Conditions:",
            "  Type           Status  Reason",
            f"  Available      {'True' if deployment['available'] else 'False'}   MinimumReplicasAvailable",
            "Events:",
        ]
        lines.extend("  " + event for event in events)
        return CommandResult(0, "\n".join(lines) + "\n", "", "supported", "kubectl.describe.deployments")
    if kind == "replicasets":
        replicaset = _find_named(resources["replicasets"], name)
        if replicaset is None:
            return _not_found("replicasets", name)
        return CommandResult(
            0,
            (
                f"Name:           {replicaset['name']}\n"
                f"Namespace:      {state.namespace}\n"
                f"Controlled By:  Deployment/{replicaset['owner']}\n"
                f"Replicas:       {replicaset['ready']} ready / {replicaset['desired']} desired\n"
            ),
            "",
            "supported",
            "kubectl.describe.replicasets",
        )
    if kind == "daemonsets":
        daemonset = _find_named(resources["daemonsets"], name)
        if daemonset is None:
            return _not_found("daemonsets", name)
        return CommandResult(
            0,
            (
                f"Name:           {daemonset['name']}\n"
                f"Namespace:      {state.namespace}\n"
                f"Node Selector:  {daemonset['node_selector']}\n"
                f"Desired:        {daemonset['desired']}\n"
                f"Ready:          {daemonset['ready']}\n"
            ),
            "",
            "supported",
            "kubectl.describe.daemonsets",
        )
    if kind == "services":
        service = _find_named(resources["services"], name)
        if service is None:
            return _not_found("services", name)
        endpoint = _find_named(resources["endpoints"], name)
        lines = [
            f"Name:              {service['name']}",
            f"Namespace:         {state.namespace}",
            f"Type:              {service['type']}",
            f"IP:                {service['cluster_ip']}",
            f"Port:              {service['ports']}",
            f"Endpoints:         {endpoint['endpoints'] if endpoint else '<none>'}",
        ]
        return CommandResult(0, "\n".join(lines) + "\n", "", "supported", "kubectl.describe.services")
    if kind == "endpoints":
        endpoint = _find_named(resources["endpoints"], name)
        if endpoint is None:
            return _not_found("endpoints", name)
        return CommandResult(
            0,
            (
                f"Name:       {endpoint['name']}\n"
                f"Namespace:  {state.namespace}\n"
                f"Endpoints:  {endpoint['endpoints'] or '<none>'}\n"
            ),
            "",
            "supported",
            "kubectl.describe.endpoints",
        )
    if kind == "endpointslices":
        endpointslice = _find_named(resources["endpointslices"], name)
        if endpointslice is None:
            return _not_found("endpointslices", name)
        return CommandResult(
            0,
            (
                f"Name:          {endpointslice['name']}\n"
                f"Namespace:     {state.namespace}\n"
                f"Service:       {endpointslice['service']}\n"
                f"Address Type:  {endpointslice['address_type']}\n"
                f"Endpoints:     {endpointslice['endpoints']}\n"
            ),
            "",
            "supported",
            "kubectl.describe.endpointslices",
        )
    if kind == "hpa":
        hpa = _find_named(resources["hpa"], name)
        if hpa is None:
            return _not_found("horizontalpodautoscalers", name)
        return CommandResult(
            0,
            (
                f"Name:         {hpa['name']}\n"
                f"Namespace:    {state.namespace}\n"
                f"Reference:    {hpa['reference']}\n"
                f"Targets:      {hpa['targets']}\n"
                f"Replicas:     {hpa['replicas']}\n"
            ),
            "",
            "supported",
            "kubectl.describe.hpa",
        )
    if kind == "nodes":
        node = _find_named(resources["nodes"], name)
        if node is None:
            return _not_found("nodes", name)
        lines = [
            f"Name:               {node['name']}",
            f"Roles:              {node['roles']}",
            f"Status:             {node['status']}",
            "Conditions:",
            f"  Ready             {node['status'] == 'Ready'}",
            "Allocated resources:",
            f"  cpu               {node['cpu_pct']}%",
            f"  memory            {node['memory_pct']}%",
        ]
        return CommandResult(0, "\n".join(lines) + "\n", "", "supported", "kubectl.describe.nodes")
    if kind == "pvc":
        pvc = _find_named(resources["pvc"], name)
        if pvc is None:
            return _not_found("persistentvolumeclaims", name)
        lines = [
            f"Name:          {pvc['name']}",
            f"Namespace:     {state.namespace}",
            f"Status:        {pvc['status']}",
            f"Capacity:      {pvc['capacity']}",
            f"Used:          {pvc['used_pct']}%",
            "Events:",
            "  Warning VolumePressure database write volume approaching capacity"
            if pvc["used_pct"] >= 90 else "  Normal Bound volume attached",
        ]
        return CommandResult(0, "\n".join(lines) + "\n", "", "supported", "kubectl.describe.pvc")
    if kind == "statefulsets":
        statefulset = _find_named(resources["statefulsets"], name)
        if statefulset is None:
            return _not_found("statefulsets", name)
        return CommandResult(
            0,
            f"Name:       {statefulset['name']}\nNamespace:  {state.namespace}\nPods Status: {statefulset['ready']}\n",
            "",
            "supported",
            "kubectl.describe.statefulsets",
        )
    if kind == "configmaps":
        configmap = _find_named(resources["configmaps"], name)
        if configmap is None:
            return _not_found("configmaps", name)
        lines = [
            f"Name:      {configmap['name']}",
            f"Namespace: {state.namespace}",
            "Data",
        ]
        lines.extend(f"  {key}: {value}" for key, value in configmap["keys"].items())
        return CommandResult(0, "\n".join(lines) + "\n", "", "supported", "kubectl.describe.configmaps")
    if kind == "secrets":
        secret = _find_named(resources["secrets"], name)
        if secret is None:
            return _not_found("secrets", name)
        return CommandResult(
            0,
            (
                f"Name:      {secret['name']}\n"
                f"Namespace: {state.namespace}\n"
                f"Type:      {secret['type']}\n"
                f"Data:      {secret['data']}\n"
            ),
            "",
            "supported",
            "kubectl.describe.secrets",
        )
    if kind == "jobs":
        job = _find_named(resources["jobs"], name)
        if job is None:
            return _not_found("jobs", name)
        return CommandResult(
            0,
            (
                f"Name:        {job['name']}\n"
                f"Namespace:   {state.namespace}\n"
                f"Completions: {job['completions']}\n"
                f"Duration:    {job['duration']}\n"
            ),
            "",
            "supported",
            "kubectl.describe.jobs",
        )
    if kind == "cronjobs":
        cronjob = _find_named(resources["cronjobs"], name)
        if cronjob is None:
            return _not_found("cronjobs", name)
        return CommandResult(
            0,
            (
                f"Name:           {cronjob['name']}\n"
                f"Namespace:      {state.namespace}\n"
                f"Schedule:       {cronjob['schedule']}\n"
                f"Suspend:        {cronjob['suspend']}\n"
                f"Active Jobs:    {cronjob['active']}\n"
                f"Last Schedule:  {cronjob['last_schedule']}\n"
            ),
            "",
            "supported",
            "kubectl.describe.cronjobs",
        )
    if kind == "serviceaccounts":
        serviceaccount = _find_named(resources["serviceaccounts"], name)
        if serviceaccount is None:
            return _not_found("serviceaccounts", name)
        return CommandResult(
            0,
            (
                f"Name:      {serviceaccount['name']}\n"
                f"Namespace: {state.namespace}\n"
                f"Secrets:   {serviceaccount['secrets']}\n"
            ),
            "",
            "supported",
            "kubectl.describe.serviceaccounts",
        )
    if kind == "ingress":
        ingress = _find_named(resources["ingress"], name)
        if ingress is None:
            return _not_found("ingress", name)
        return CommandResult(
            0,
            (
                f"Name:      {ingress['name']}\n"
                f"Namespace: {state.namespace}\n"
                f"Class:     {ingress['class']}\n"
                f"Hosts:     {ingress['hosts']}\n"
                f"Address:   {ingress['address']}\n"
            ),
            "",
            "supported",
            "kubectl.describe.ingress",
        )
    if kind == "namespaces":
        namespace = _find_named(resources["namespaces"], name)
        if namespace is None:
            return _not_found("namespaces", name)
        return CommandResult(
            0,
            f"Name:   {namespace['name']}\nStatus: {namespace['status']}\n",
            "",
            "supported",
            "kubectl.describe.namespaces",
        )
    return _unsupported(parsed, f"kubectl describe {kind}")


def _render_logs(state: SimulationState, parsed: ParsedCommand) -> str:
    component = _component_from_name(parsed.resource_name, state.components)
    lines = []
    for profile in state.profiles():
        if component in profile.affected_components:
            lines.extend(profile.logs)
    if not lines:
        lines = [
            f"{component or parsed.resource_name} health probe ok",
            f"{component or parsed.resource_name} processed request batch without anomaly",
        ]
    now = _format_dt(state.clock.now())
    return "".join(f"{now} {line}\n" for line in lines[-20:])


def _render_top(state: SimulationState, kind: str) -> str:
    resources = resource_snapshot(state)
    if kind == "pods":
        return _table(["NAME", "CPU(cores)", "MEMORY(bytes)"], [
            [pod["name"], f"{pod['cpu_m']}m", f"{pod['memory_mi']}Mi"]
            for pod in resources["pods"]
        ])
    return _table(["NAME", "CPU(cores)", "CPU%", "MEMORY(bytes)", "MEMORY%"], [
        [node["name"], f"{node['cpu_m']}m", f"{node['cpu_pct']}%", f"{node['memory_mi']}Mi", f"{node['memory_pct']}%"]
        for node in resources["nodes"]
    ])


def _render_kubectl_version() -> str:
    return (
        "Client Version: v1.29.4\n"
        "Kustomize Version: v5.0.4\n"
        "Server Version: v1.29.4-amc\n"
    )


def _render_kubectl_api_versions() -> str:
    versions = [
        "v1",
        "apps/v1",
        "autoscaling/v2",
        "batch/v1",
        "discovery.k8s.io/v1",
        "networking.k8s.io/v1",
        "metrics.k8s.io/v1beta1",
        "authorization.k8s.io/v1",
    ]
    return "\n".join(versions) + "\n"


def _render_kubectl_api_resources() -> str:
    rows = [
        ["pods", "po", "true", "Pod"],
        ["services", "svc", "true", "Service"],
        ["configmaps", "cm", "true", "ConfigMap"],
        ["secrets", "", "true", "Secret"],
        ["endpoints", "ep", "true", "Endpoints"],
        ["serviceaccounts", "sa", "true", "ServiceAccount"],
        ["nodes", "no", "false", "Node"],
        ["deployments", "deploy", "true", "Deployment"],
        ["replicasets", "rs", "true", "ReplicaSet"],
        ["daemonsets", "ds", "true", "DaemonSet"],
        ["statefulsets", "sts", "true", "StatefulSet"],
        ["horizontalpodautoscalers", "hpa", "true", "HorizontalPodAutoscaler"],
        ["jobs", "", "true", "Job"],
        ["cronjobs", "cj", "true", "CronJob"],
        ["ingresses", "ing", "true", "Ingress"],
        ["endpointslices", "", "true", "EndpointSlice"],
    ]
    return _table(["NAME", "SHORTNAMES", "NAMESPACED", "KIND"], rows)


def _render_kubectl_cluster_info() -> str:
    return (
        "Kubernetes control plane is running at http://127.0.0.1:8088\n"
        "AMC simulator debug console is running at http://127.0.0.1:8088/debug\n"
    )


def _render_rollout_status(state: SimulationState, parsed: ParsedCommand) -> str:
    component = parsed.resource_name or "apigateway"
    if component.startswith("deployment/"):
        component = component.split("/", 1)[1]
    if "deploy_bad_canary_rollback" in state.active_scenarios and component == "apigateway":
        return (
            "deployment \"apigateway\" successfully rolled out\n"
            "note: release was rolled back from failed canary revision\n"
        )
    health = _component_health(state, component)
    rollout_notes = _component_rollout_notes(state, component)
    if health["deployment_status"] != "Healthy":
        output = f"waiting for deployment \"{component}\" rollout to finish: {health['deployment_status']}\n"
        if rollout_notes:
            output += "\n".join(f"note: {note}" for note in rollout_notes) + "\n"
        return output
    output = f"deployment \"{component}\" successfully rolled out\n"
    if rollout_notes:
        output += "\n".join(f"note: {note}" for note in rollout_notes) + "\n"
    return output


def _render_rollout_history(state: SimulationState, parsed: ParsedCommand) -> str:
    component = parsed.resource_name or "apigateway"
    if component.startswith("deployment/"):
        component = component.split("/", 1)[1]
    rows = [["1", "simulated-saas-0.2.0", "baseline deployment"]]
    if "deploy_bad_canary_rollback" in state.active_scenarios and component == "apigateway":
        rows.extend([
            ["2", "simulated-saas-0.3.0-canary", "canary readiness failed"],
            ["3", "simulated-saas-0.3.0", "rollback to stable revision"],
        ])
    else:
        description = "current deployment"
        rollout_notes = _component_rollout_notes(state, component)
        if rollout_notes:
            description = "; ".join(rollout_notes)
        rows.append(["2", "simulated-saas-0.3.0", description])
    return f"deployment.apps/{component}\n" + _table(["REVISION", "CHANGE-CAUSE", "DESCRIPTION"], rows)


def _render_rollout_restart(state: SimulationState, parsed: ParsedCommand) -> str:
    component = parsed.resource_name or "apigateway"
    if component.startswith("deployment/"):
        component = component.split("/", 1)[1]
    now = state.clock.now()
    state.mutations.set_workload(
        component,
        now=now,
        deployment_status="Restarting",
        pod_status="Running",
        restarts_delta=1,
    )
    state.mutations.record_event(
        "Normal",
        "RolloutRestart",
        f"deployment/{component}",
        f"deployment {component} restarted by simulator command",
        now,
    )
    return f"deployment.apps/{component} restarted\n"


def _render_scale(state: SimulationState, parsed: ParsedCommand) -> str:
    if parsed.resource_kind not in {"deployments", "deployment", "deploy", ""}:
        return f"{_normalized_resource_prefix(parsed.resource_kind)}/{parsed.resource_name} scaled\n"
    component = parsed.resource_name or "apigateway"
    replicas = _parsed_replicas(parsed)
    now = state.clock.now()
    state.mutations.set_workload(
        component,
        now=now,
        replicas=replicas,
        ready_replicas=replicas,
        deployment_status="Healthy" if replicas else "ScaledToZero",
        pod_status="Running",
    )
    state.mutations.record_event(
        "Normal",
        "ScalingReplicaSet",
        f"deployment/{component}",
        f"scaled deployment {component} to {replicas} replicas",
        now,
    )
    return f"deployment.apps/{component} scaled\n"


def _render_delete(state: SimulationState, parsed: ParsedCommand) -> str:
    kind = parsed.resource_kind
    name = parsed.resource_name
    now = state.clock.now()
    if kind in {"pods", "pod"} and name:
        state.mutations.delete_pod(name, now=now)
        return f"pod \"{name}\" deleted\n"
    if kind in {"deployments", "deployment", "deploy"} and name:
        state.mutations.set_workload(
            name,
            now=now,
            replicas=0,
            ready_replicas=0,
            deployment_status="Deleted",
            pod_status="Terminating",
            deleted=True,
        )
        state.mutations.record_event(
            "Normal",
            "Deleted",
            f"deployment/{name}",
            f"deployment {name} deleted from simulator state",
            now,
        )
        return f"deployment.apps \"{name}\" deleted\n"
    snapshot_kind = _mutation_snapshot_kind(kind)
    if snapshot_kind and name:
        state.mutations.delete_resource(snapshot_kind, name, now=now)
    prefix = _resource_prefix(snapshot_kind or _KIND_ALIASES.get(kind, kind) or "resource")
    return f"{prefix} \"{name}\" deleted\n"


def _render_apply(state: SimulationState, parsed: ParsedCommand) -> str:
    filename = parsed.flags.get("-f") or parsed.flags.get("--filename") or "manifest"
    now = state.clock.now()
    kind = parsed.resource_kind
    name = parsed.resource_name
    if parsed.verb == "apply":
        kind, name = _resource_from_manifest_name(str(filename))
    snapshot_kind = _mutation_snapshot_kind(kind)
    if snapshot_kind and name:
        state.mutations.put_resource(
            snapshot_kind,
            name,
            _generic_resource_row(state, snapshot_kind, name, payload={}, parsed=parsed),
            now=now,
        )
    else:
        state.mutations.record_event(
            "Normal",
            "Applied",
            "manifest/simulated",
            f"{parsed.verb} accepted {filename}; simulator state reconciled",
            now,
        )
    action = "configured" if parsed.verb == "apply" else "created"
    target = f"{_resource_prefix(snapshot_kind)}/{name}" if snapshot_kind and name else str(filename)
    return f"{target} {action}\n"


def _resource_from_manifest_name(filename: str) -> tuple[str, str]:
    stem = Path(filename).name
    for suffix in (".yaml", ".yml", ".json"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
    tokens = [token for token in stem.replace("_", "-").split("-") if token]
    aliases = {
        "configmap": "configmaps",
        "cm": "configmaps",
        "secret": "secrets",
        "service": "services",
        "svc": "services",
        "deployment": "deployments",
        "deploy": "deployments",
        "job": "jobs",
        "cronjob": "cronjobs",
        "ingress": "ingress",
        "hpa": "hpa",
        "serviceaccount": "serviceaccounts",
    }
    if tokens and tokens[0] in aliases and len(tokens) > 1:
        return aliases[tokens[0]], "-".join(tokens[1:])
    if tokens and tokens[-1] in aliases and len(tokens) > 1:
        return aliases[tokens[-1]], "-".join(tokens[:-1])
    return "configmaps", stem or "simulated-manifest"


def _mutation_snapshot_kind(kind: str) -> str:
    normalized = _normalize_kind(kind)
    aliases = {
        "horizontalpodautoscalers": "hpa",
        "persistentvolumeclaims": "pvc",
        "ingresses": "ingress",
        "manifest": "configmaps",
    }
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in _MUTATION_SNAPSHOT_KINDS else ""


def _record_continuous_generation_failure(
    state: SimulationState,
    exc: BaseException,
) -> None:
    with state.generation.lock:
        state.generation.last_error = str(exc) or exc.__class__.__name__
        state.generation.thread = "failed"


def _generic_resource_row(
    state: SimulationState,
    kind: str,
    name: str,
    *,
    payload: dict[str, Any],
    parsed: ParsedCommand | None = None,
) -> dict[str, Any]:
    spec = payload.get("spec") if isinstance(payload.get("spec"), dict) else {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    string_data = payload.get("stringData") if isinstance(payload.get("stringData"), dict) else {}
    if kind == "configmaps":
        keys = {str(key): str(value) for key, value in data.items()} or _configmap_keys_from_flags(parsed)
        if not keys:
            keys = {"simulated": "true"}
        return {"name": name, "data": len(keys), "age": "0s", "keys": keys}
    if kind == "secrets":
        secret_data = {str(key): str(value) for key, value in {**data, **string_data}.items()}
        return {"name": name, "type": payload.get("type", "Opaque"), "data": len(secret_data) or 1, "age": "0s"}
    if kind == "services":
        service_type = str(spec.get("type") or "ClusterIP")
        ports = spec.get("ports") if isinstance(spec.get("ports"), list) else []
        port = ports[0].get("port", 8080) if ports and isinstance(ports[0], dict) else 8080
        return {
            "name": name,
            "type": service_type,
            "cluster_ip": str(spec.get("clusterIP") or _stable_cluster_ip(name)),
            "external_ip": "<none>",
            "ports": f"{port}/TCP",
            "age": "0s",
        }
    if kind == "deployments":
        replicas = _payload_replicas(payload)
        if replicas is None:
            replicas = 1
        return {
            "name": name,
            "ready": f"{replicas}/{replicas}",
            "up_to_date": replicas,
            "available": replicas,
            "age": "0s",
            "status": "Healthy" if replicas else "ScaledToZero",
        }
    if kind == "serviceaccounts":
        return {"name": name, "secrets": len(payload.get("secrets", [])), "age": "0s"}
    if kind == "hpa":
        min_replicas = int(spec.get("minReplicas", 1) or 1)
        max_replicas = int(spec.get("maxReplicas", 8) or 8)
        target = spec.get("scaleTargetRef") if isinstance(spec.get("scaleTargetRef"), dict) else {}
        target_name = str(target.get("name") or name)
        return {
            "name": name,
            "reference": f"{target.get('kind', 'Deployment')}/{target_name}",
            "targets": "0%/80%",
            "minpods": min_replicas,
            "maxpods": max_replicas,
            "replicas": min_replicas,
            "age": "0s",
        }
    if kind == "jobs":
        completions = int(spec.get("completions", 1) or 1)
        return {"name": name, "completions": f"0/{completions}", "duration": "0s", "age": "0s"}
    if kind == "cronjobs":
        schedule = str(spec.get("schedule") or (parsed.flags.get("--schedule") if parsed else "") or "* * * * *")
        return {"name": name, "schedule": schedule, "suspend": "False", "active": 0, "last_schedule": "<none>", "age": "0s"}
    if kind == "pvc":
        requests = spec.get("resources", {}).get("requests", {}) if isinstance(spec.get("resources"), dict) else {}
        access_modes = spec.get("accessModes", ["RWO"])
        if not isinstance(access_modes, list):
            access_modes = ["RWO"]
        return {
            "name": name,
            "status": "Bound",
            "volume": f"pvc-{name}",
            "capacity": str(requests.get("storage", "1Gi")),
            "access_modes": ",".join(str(mode) for mode in access_modes),
            "storageclass": str(spec.get("storageClassName", "gp3")),
            "age": "0s",
            "used_pct": 1,
        }
    if kind == "statefulsets":
        replicas = _payload_replicas(payload)
        if replicas is None:
            replicas = 1
        return {"name": name, "ready": f"{replicas}/{replicas}", "age": "0s"}
    if kind == "daemonsets":
        nodes = _node_rows(state)
        return {
            "name": name,
            "desired": len(nodes),
            "current": len(nodes),
            "ready": len(nodes),
            "up_to_date": len(nodes),
            "available": len(nodes),
            "node_selector": "kubernetes.io/os=linux",
            "age": "0s",
        }
    if kind == "ingress":
        rules = spec.get("rules") if isinstance(spec.get("rules"), list) else []
        host = rules[0].get("host") if rules and isinstance(rules[0], dict) else f"{name}.simulated-saas.local"
        return {"name": name, "class": spec.get("ingressClassName", "nginx"), "hosts": host, "address": "10.0.0.20", "ports": "80,443", "age": "0s"}
    return {"name": name, "age": "0s"}


def _configmap_keys_from_flags(parsed: ParsedCommand | None) -> dict[str, str]:
    if parsed is None:
        return {}
    literal = parsed.flags.get("--from-literal")
    if isinstance(literal, str) and literal:
        key, _, value = literal.partition("=")
        return {key or "literal": value or "true"}
    return {}


def _parsed_replicas(parsed: ParsedCommand) -> int:
    value = parsed.flags.get("--replicas")
    if value is None:
        for token in parsed.positionals:
            if token.startswith("--replicas="):
                value = token.split("=", 1)[1]
                break
    try:
        return max(0, int(str(value)))
    except (TypeError, ValueError):
        return 1


def _render_wait(state: SimulationState, parsed: ParsedCommand) -> str:
    component = parsed.resource_name or "apigateway"
    health = _component_health(state, component)
    condition = str(parsed.flags.get("--for") or "condition=available")
    prefix = _normalized_resource_prefix(parsed.resource_kind)
    if health["deployment_status"] in {"Healthy", "RolledBack"}:
        return f"{prefix}/{component} condition met: {condition}\n"
    return f"{prefix}/{component} condition pending: {health['deployment_status']}\n"


def _render_exec(state: SimulationState, parsed: ParsedCommand) -> str:
    pod_name = parsed.resource_name
    component = _component_from_name(pod_name, state.components)
    if len(parsed.positionals) > 2 and "--" in parsed.positionals:
        command = " ".join(parsed.positionals[parsed.positionals.index("--") + 1:])
    else:
        command = " ".join(parsed.positionals[2:]) or "healthcheck"
    if any(token in command for token in {"env", "printenv"}):
        return (
            f"SERVICE_NAME={component}\n"
            f"NAMESPACE={state.namespace}\n"
            f"SCENARIOS={','.join(state.active_scenarios)}\n"
        )
    if "curl" in command:
        return f"HTTP/1.1 200 OK\nx-amc-component: {component}\n\nok\n"
    return f"{pod_name}: simulated exec completed for `{command}`\n"


def _render_port_forward(parsed: ParsedCommand) -> str:
    port = parsed.positionals[2] if len(parsed.positionals) > 2 else "8080:8080"
    return (
        f"Forwarding from 127.0.0.1:{port.split(':', 1)[0]} -> {port.split(':')[-1]}\n"
        "Forwarding from [::1]:"
        f"{port.split(':', 1)[0]} -> {port.split(':')[-1]}\n"
        "simulator note: stream held open only in real kubectl; command API returns immediately\n"
    )


def _render_helm_list(state: SimulationState) -> str:
    with state.mutations.lock:
        if state.mutations.release.uninstalled:
            return _table(
                ["NAME", "NAMESPACE", "REVISION", "UPDATED", "STATUS", "CHART", "APP VERSION"],
                [],
            )
    release = _helm_release(state)
    return _table(["NAME", "NAMESPACE", "REVISION", "UPDATED", "STATUS", "CHART", "APP VERSION"], [[
        release["name"], release["namespace"], str(release["revision"]),
        release["updated"], release["status"], release["chart"], release["app_version"],
    ]])


def _render_helm_status(state: SimulationState) -> str:
    release = _helm_release(state)
    return (
        f"NAME: {release['name']}\n"
        f"LAST DEPLOYED: {release['updated']}\n"
        f"NAMESPACE: {release['namespace']}\n"
        f"STATUS: {release['status']}\n"
        f"REVISION: {release['revision']}\n"
        f"NOTES:\n{_helm_notes(state)}\n"
    )


def _render_helm_history(state: SimulationState) -> str:
    rows = []
    now = state.clock.now()
    for revision in _helm_release_revisions(state):
        version = int(revision["version"])
        if version == 1:
            updated = "2026-03-01 00:00:00"
        elif version == 2:
            updated = "2026-03-08 00:00:00"
        else:
            updated = _format_dt(now)
        rows.append([
            str(version),
            updated,
            str(revision["status"]),
            DEFAULT_CHART,
            str(revision["description"]),
        ])
    return _table(["REVISION", "UPDATED", "STATUS", "CHART", "DESCRIPTION"], rows)


def _render_helm_env() -> str:
    return (
        "HELM_BIN=\"helm\"\n"
        "HELM_CACHE_HOME=\"/tmp/amc/helm/cache\"\n"
        "HELM_CONFIG_HOME=\"/tmp/amc/helm/config\"\n"
        "HELM_DATA_HOME=\"/tmp/amc/helm/data\"\n"
        "HELM_NAMESPACE=\"saas-prod\"\n"
        "HELM_DRIVER=\"secrets\"\n"
    )


def _render_helm_get(state: SimulationState, kind: str) -> str:
    if kind == "values":
        with state.mutations.lock:
            values = dict(state.mutations.release.values)
        value_lines = "".join(
            f"{key}: {value}\n"
            for key, value in sorted(values.items())
        )
        return (
            "replicaCount: 3\n"
            f"namespace: {state.namespace}\n"
            "observability:\n"
            "  otel: true\n"
            f"scenarios: {json.dumps(list(state.active_scenarios))}\n"
            + value_lines
        )
    if kind == "manifest":
        deployments = "\n".join(
            f"---\napiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: {component}\n"
            f"  namespace: {state.namespace}\n"
            for component in state.components
        )
        return deployments or "---\n"
    if kind == "hooks":
        return "HOOKS:\n(no hooks defined for simulated-saas)\n"
    if kind == "all":
        return (
            "COMPUTED VALUES:\n"
            + _render_helm_get(state, "values")
            + "\nMANIFEST:\n"
            + _render_helm_get(state, "manifest")
            + "\nNOTES:\n"
            + _render_helm_get(state, "notes")
        )
    return _helm_notes(state) + "\n"


def _render_helm_test(state: SimulationState) -> str:
    rows = [["simulated-saas-connectivity", "Succeeded", _format_dt(state.clock.now())]]
    if "deploy_bad_canary_rollback" in state.active_scenarios:
        rows.append(["simulated-saas-canary", "SucceededAfterRollback", _format_dt(state.clock.now())])
    return _table(["NAME", "STATUS", "LAST RUN"], rows)


def _render_helm_install(state: SimulationState, parsed: ParsedCommand) -> str:
    release = parsed.resource_name or DEFAULT_RELEASE
    now = state.clock.now()
    values = _helm_value_overrides(parsed)
    revisions = [{
        "version": 1,
        "status": "deployed",
        "description": f"Install applied to {release}",
    }]
    state.mutations.set_revisions(revisions, now=now, uninstalled=False)
    if values:
        state.mutations.set_release_values(values, now=now)
    state.mutations.record_event(
        "Normal",
        "HelmInstall",
        f"release/{release}",
        f"release {release} installed by simulator command",
        now,
    )
    return (
        f"NAME: {release}\n"
        f"LAST DEPLOYED: {_format_dt(now)}\n"
        f"NAMESPACE: {state.namespace}\n"
        "STATUS: deployed\n"
        "REVISION: 1\n"
        "NOTE: simulator release state installed.\n"
    )


def _render_helm_upgrade(state: SimulationState, parsed: ParsedCommand) -> str:
    release = parsed.resource_name or DEFAULT_RELEASE
    mode = "dry run" if "--dry-run" in parsed.flags else "simulated"
    current = _helm_release_revisions(state)
    if "--dry-run" not in parsed.flags:
        now = state.clock.now()
        values = _helm_value_overrides(parsed)
        revisions = [
            {**revision, "status": "superseded" if revision["status"] == "deployed" else revision["status"]}
            for revision in current
        ]
        revisions.append({
            "version": int(revisions[-1]["version"]) + 1 if revisions else 1,
            "status": "deployed",
            "description": f"Upgrade applied to {release}",
        })
        state.mutations.set_revisions(revisions, now=now, uninstalled=False)
        if values:
            state.mutations.set_release_values(values, now=now)
        state.mutations.record_event(
            "Normal",
            "HelmUpgrade",
            f"release/{release}",
            f"release {release} upgraded by simulator command",
            now,
        )
    return (
        f"Release \"{release}\" has been upgraded ({mode}).\n"
        f"NAMESPACE: {state.namespace}\n"
        f"STATUS: {_helm_release(state)['status']}\n"
        "NOTE: simulator release state updated.\n"
    )


def _helm_value_overrides(parsed: ParsedCommand) -> dict[str, str]:
    values: dict[str, str] = {}
    for flag in ("--set", "--set-string"):
        raw = parsed.flags.get(flag)
        if isinstance(raw, str):
            for item in raw.split(","):
                key, _, value = item.partition("=")
                if key:
                    values[key] = value or "true"
    values_file = parsed.flags.get("--values")
    if values_file is None:
        values_file = parsed.flags.get("-f")
    if isinstance(values_file, str) and values_file:
        values["values_file"] = values_file
    return values


def _render_helm_rollback(state: SimulationState, parsed: ParsedCommand) -> str:
    release = parsed.resource_name or DEFAULT_RELEASE
    revision = parsed.positionals[2] if len(parsed.positionals) > 2 else "previous"
    now = state.clock.now()
    current = _helm_release_revisions(state)
    revisions = [
        {**item, "status": "superseded" if item["status"] == "deployed" else item["status"]}
        for item in current
    ]
    revisions.append({
        "version": int(revisions[-1]["version"]) + 1 if revisions else 1,
        "status": "deployed",
        "description": f"Rollback to revision {revision}",
    })
    state.mutations.set_revisions(revisions, now=now, uninstalled=False)
    state.mutations.record_event(
        "Normal",
        "HelmRollback",
        f"release/{release}",
        f"release {release} rolled back to revision {revision}",
        now,
    )
    return (
        f"Rollback was a success for release \"{release}\" to revision {revision}.\n"
        f"NAMESPACE: {state.namespace}\n"
        "NOTE: simulator release state updated.\n"
    )


def _not_found(kind: str, name: str) -> CommandResult:
    return CommandResult(
        1,
        "",
        f"Error from server (NotFound): {kind} \"{name}\" not found\n",
        "supported",
        "kubectl.not_found",
    )


_DEPLOYMENT_STATUS_PRIORITY = {
    "Healthy": 0,
    "TrafficBurst": 1,
    "ScenarioInfluenced": 1,
    "RecoveredAfterRollback": 1,
    "RetryPressure": 2,
    "CacheMissPressure": 2,
    "DatabaseBackpressure": 2,
    "AuthDependencyDegraded": 2,
    "DNSDependencyFailure": 2,
    "NetworkDegraded": 2,
    "FallbackServing": 2,
    "InferenceFallback": 2,
    "EndpointChurn": 2,
    "Backpressure": 2,
    "TelemetryBacklog": 2,
    "TenantImport": 2,
    "ContextCachePressure": 2,
    "HotKeyChurn": 2,
    "JWKSCacheChurn": 2,
    "DependencyDegraded": 3,
    "RateLimited": 3,
    "CPUSaturated": 3,
    "CacheDegraded": 3,
    "ReadPressure": 3,
    "DatabaseStall": 3,
    "QueueBacklog": 3,
    "HealthCheckFlap": 3,
    "ObjectStore5xx": 3,
    "Upstream5xx": 3,
    "IndexRebuild": 3,
    "RetrievalDegraded": 3,
    "QueueOverflow": 3,
    "Provider5xx": 3,
    "CheckoutDegraded": 3,
    "JWKSCacheMiss": 3,
    "TokenValidationSlow": 3,
    "IngestLag": 3,
    "LLMSurge": 3,
    "LargeContext": 3,
    "LookupPressure": 3,
    "ProviderRateLimited": 3,
    "BatchPressure": 3,
    "BandwidthPressure": 3,
    "BatchWritePressure": 3,
    "BatchEvictions": 3,
    "ViralTraffic": 3,
    "GatewayPressure": 3,
    "MetadataWritePressure": 3,
    "GPUFragmented": 3,
    "RegionalFailover": 3,
    "FailoverSaturated": 3,
    "ReplicationLag": 3,
    "FailoverPressure": 3,
    "ReplayBacklog": 3,
    "ProviderUnavailable": 3,
    "ProviderOutage": 3,
    "FallbackPressure": 3,
    "StoragePressure": 3,
    "StorageWait": 3,
    "UploadDegraded": 3,
    "RolledBack": 3,
    "NetworkPartition": 3,
    "CertRotation": 3,
    "JWKSRotation": 3,
    "TokenValidationFailing": 3,
    "WriteBacklog": 3,
    "PartialOutage": 4,
    "AZIsolated": 4,
    "Degraded": 4,
}
_POD_STATUS_PRIORITY = {
    "Running": 0,
    "Pending": 1,
    "CrashLoopBackOff": 3,
    "Error": 4,
}


def _component_health(state: SimulationState, component: str) -> dict[str, Any]:
    replicas = _replica_count(state, component)
    health = {
        "pod_status": "Running",
        "deployment_status": "Healthy",
        "ready": "1/1",
        "ready_replicas": replicas,
        "restarts": 0,
        "cpu_pct": 36,
        "cpu_m": 180,
        "memory_mi": 384,
        "memory_pct": 42,
        "pvc_used_pct": 61,
    }
    impacts = _component_impacts(state, component)
    for impact in impacts:
        _apply_component_impact(health, impact, replicas)
    scenarios = _component_scenarios(state, component)
    if scenarios and not impacts:
        health.update({"deployment_status": "ScenarioInfluenced", "cpu_pct": 55, "cpu_m": 550})
    with state.mutations.lock:
        mutation = state.mutations.workloads.get(component)
        if mutation is not None:
            if mutation.deployment_status:
                health["deployment_status"] = mutation.deployment_status
            if mutation.pod_status:
                health["pod_status"] = mutation.pod_status
            if mutation.ready_replicas is not None:
                health["ready_replicas"] = mutation.ready_replicas
            if mutation.restarts_delta:
                health["restarts"] += mutation.restarts_delta
            if mutation.deleted:
                health.update({
                    "deployment_status": "Deleted",
                    "pod_status": "Terminating",
                    "ready_replicas": 0,
                    "ready": "0/1",
                })
    health["ready_replicas"] = max(0, min(replicas, health["ready_replicas"]))
    return health


def _component_impacts(state: SimulationState, component: str) -> list[OpsComponentImpact]:
    return [
        impact
        for profile in state.profiles()
        for impact in profile.impacts
        if impact.component == component
    ]


def _apply_component_impact(
    health: dict[str, Any],
    impact: OpsComponentImpact,
    replicas: int,
) -> None:
    if _status_priority(
        impact.deployment_status,
        _DEPLOYMENT_STATUS_PRIORITY,
    ) >= _status_priority(
        health["deployment_status"],
        _DEPLOYMENT_STATUS_PRIORITY,
    ):
        health["deployment_status"] = impact.deployment_status
    if _status_priority(impact.pod_status, _POD_STATUS_PRIORITY) >= _status_priority(
        health["pod_status"],
        _POD_STATUS_PRIORITY,
    ):
        health["pod_status"] = impact.pod_status
    if impact.ready:
        health["ready"] = impact.ready
    elif impact.pod_status != "Running":
        health["ready"] = "0/1"
    if impact.ready_replicas is not None:
        health["ready_replicas"] = impact.ready_replicas
    elif impact.ready_replicas_delta:
        health["ready_replicas"] += impact.ready_replicas_delta
    health["ready_replicas"] = max(0, min(replicas, health["ready_replicas"]))
    health["restarts"] += impact.restarts
    if impact.cpu_pct is not None:
        health["cpu_pct"] = max(health["cpu_pct"], impact.cpu_pct)
        health["cpu_m"] = max(health["cpu_m"], impact.cpu_m or impact.cpu_pct * 10)
    elif impact.cpu_m is not None:
        health["cpu_m"] = max(health["cpu_m"], impact.cpu_m)
    if impact.memory_mi is not None:
        health["memory_mi"] = max(health["memory_mi"], impact.memory_mi)
    if impact.memory_pct is not None:
        health["memory_pct"] = max(health["memory_pct"], impact.memory_pct)
    if impact.pvc_used_pct is not None:
        health["pvc_used_pct"] = max(health["pvc_used_pct"], impact.pvc_used_pct)


def _status_priority(status: str, priority: dict[str, int]) -> int:
    return priority.get(status, 2)


def _component_scenarios(state: SimulationState, component: str) -> list[str]:
    matches = []
    for scenario_id in state.active_scenarios:
        profile = OPS_SCENARIO_PROFILES.get(scenario_id)
        if profile is not None:
            affected = set(profile.affected_components)
        else:
            affected = set(state.legacy.SCENARIOS[scenario_id].components_touched)
        if component in affected:
            matches.append(scenario_id)
    return matches


def _component_events(state: SimulationState, component: str) -> list[str]:
    events: list[str] = []
    for profile in state.profiles():
        if component in profile.affected_components:
            events.extend(profile.events)
    if not events:
        events.append(f"Normal Healthy {component} probes passing")
    with state.mutations.lock:
        for event in state.mutations.extra_events:
            obj = event.get("object", "")
            if obj.endswith(f"/{component}") or obj.startswith(f"pod/{component}-"):
                events.append(
                    f"{event.get('type', 'Normal')} {event.get('reason', 'Mutation')} "
                    f"{event.get('message', '')}".strip()
                )
    return events


def _component_rollout_notes(state: SimulationState, component: str) -> list[str]:
    notes = []
    for profile in state.profiles():
        if component in profile.affected_components:
            notes.append(profile.rollout_note or profile.summary)
    return notes


def _event_rows(state: SimulationState) -> list[dict[str, str]]:
    rows = []
    now = _format_dt(state.clock.now())
    for profile in state.profiles():
        target = profile.affected_components[0] if profile.affected_components else "cluster"
        for event in profile.events:
            parts = event.split(" ", 2)
            event_type = parts[0] if parts else "Normal"
            reason = parts[1] if len(parts) > 1 else "Scenario"
            message = parts[2] if len(parts) > 2 else event
            rows.append({
                "last_seen": now,
                "type": event_type,
                "reason": reason,
                "object": f"pod/{_pod_name(target, 0)}",
                "message": message,
            })
    if not rows:
        rows.append({
            "last_seen": now,
            "type": "Normal",
            "reason": "Healthy",
            "object": "deployment/simulated-saas",
            "message": "all simulated workloads are healthy",
        })
    with state.mutations.lock:
        rows.extend(dict(event) for event in state.mutations.extra_events)
    return rows


def _node_rows(state: SimulationState) -> list[dict[str, Any]]:
    partition = "network_partition_az_split" in state.active_scenarios
    return [
        {
            "name": "ip-10-0-1-21",
            "status": "Ready",
            "roles": "worker",
            "age": "30d",
            "version": "v1.29.4",
            "cpu_m": 2100,
            "cpu_pct": 52,
            "memory_mi": 9240,
            "memory_pct": 58,
        },
        {
            "name": "ip-10-0-2-17",
            "status": "Ready",
            "roles": "worker",
            "age": "30d",
            "version": "v1.29.4",
            "cpu_m": 1840,
            "cpu_pct": 46,
            "memory_mi": 8120,
            "memory_pct": 51,
        },
        {
            "name": "ip-10-0-3-42",
            "status": "NotReady" if partition else "Ready",
            "roles": "worker",
            "age": "30d",
            "version": "v1.29.4",
            "cpu_m": 2600 if partition else 1760,
            "cpu_pct": 78 if partition else 44,
            "memory_mi": 10400 if partition else 7900,
            "memory_pct": 73 if partition else 49,
        },
    ]


def _helm_release(state: SimulationState) -> dict[str, Any]:
    revisions = _helm_release_revisions(state)
    current = revisions[-1]
    return {
        "name": DEFAULT_RELEASE,
        "namespace": state.namespace,
        "revision": int(current["version"]),
        "updated": _format_dt(state.clock.now()),
        "status": str(current["status"]),
        "chart": DEFAULT_CHART,
        "app_version": "0.3.0",
    }


def _helm_notes(state: SimulationState) -> str:
    if not state.profiles():
        return "Run kubectl get pods -n saas-prod for workload state."
    return "\n".join(
        f"- {profile.helm_notes}"
        for profile in state.profiles()
    )


def _helm_current_description(state: SimulationState) -> str:
    summaries = [profile.summary for profile in state.profiles()]
    if not summaries:
        return "Baseline config"
    description = "; ".join(summaries)
    if len(description) > 160:
        description = description[:157].rstrip() + "..."
    return description


def _replica_count(state: SimulationState, component: str) -> int:
    with state.mutations.lock:
        mutation = state.mutations.workloads.get(component)
        if mutation is not None and mutation.replicas is not None:
            return mutation.replicas
    if getattr(state.args, "instances_per_component", 1) > 1:
        return int(state.args.instances_per_component)
    if component in {"apigateway", "authservice", "cacheservice"}:
        return 3
    return 1


def _pod_name(component: str, index: int) -> str:
    if component == "database":
        return f"database-{index}"
    return f"{component}-{index}"


def _component_from_name(name: str, components: tuple[str, ...]) -> str:
    for component in components:
        if name == component or name.startswith(component + "-"):
            return component
    return name.split("-", 1)[0] if name else ""


def _component_from_pod_name(name: str) -> str:
    if not name:
        return ""
    prefix, _, suffix = name.rpartition("-")
    return prefix if suffix.isdigit() else name.split("-", 1)[0]


def _stable_cluster_ip(component: str) -> str:
    value = sum(ord(ch) for ch in component)
    return f"10.96.{value % 200}.{(value // 3) % 240 + 10}"


def _find_named(rows: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    for row in rows:
        if row.get("name") == name:
            return row
    return None


def _table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(header) for header in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    lines = ["  ".join(header.ljust(widths[i]) for i, header in enumerate(headers))]
    for row in rows:
        lines.append("  ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row)))
    return "\n".join(lines) + "\n"


def command_fingerprint(parsed: ParsedCommand, support_status: str) -> str:
    if support_status == "supported":
        return f"{parsed.family} {parsed.verb} {parsed.resource_kind}".strip()
    bits = [
        parsed.family or "unknown",
        parsed.verb or "<missing-verb>",
        parsed.resource_kind or "<missing-kind>",
    ]
    unknown_flags = sorted(
        key for key in parsed.flags
        if key not in {"namespace", "-o", "--output", "-l", "--selector", "-A", "--all-namespaces"}
    )
    if unknown_flags:
        bits.append("flags=" + ",".join(unknown_flags))
    return " ".join(bits)


def guess_intent(parsed: ParsedCommand) -> str:
    if parsed.family == "kubectl":
        if parsed.verb:
            return f"Add kubectl renderer for verb={parsed.verb!r}, kind={parsed.resource_kind or '<none>'!r}."
        return "Add support for the kubectl invocation shape."
    if parsed.family == "helm":
        return f"Add helm renderer for verb={parsed.verb or '<none>'!r}, topic={parsed.resource_kind or '<none>'!r}."
    return "Decide whether this client command belongs in the simulator surface."


def _preview(value: str, limit: int = 240) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def _redact_command_for_trace(parsed: ParsedCommand) -> str:
    if not parsed.argv:
        return parsed.raw_input
    return shlex.join(_redact_argv(parsed.argv))


def _redact_argv(argv: tuple[str, ...]) -> tuple[str, ...]:
    redacted: list[str] = []
    redact_next = False
    for token in argv:
        if redact_next:
            redacted.append("***")
            redact_next = False
            continue
        if _is_sensitive_flag_name(token):
            redacted.append(token)
            redact_next = True
            continue
        if token.startswith("--") and "=" in token:
            key, value = token.split("=", 1)
            if _is_sensitive_flag_name(key):
                redacted.append(f"{key}=***")
                continue
            redacted.append(f"{key}={value}")
            continue
        redacted.append(token)
    return tuple(redacted)


def _redact_parsed_flags(flags: dict[str, Any]) -> dict[str, Any]:
    return {
        key: ("***" if _is_sensitive_flag_name(str(key)) else value)
        for key, value in flags.items()
    }


def _is_sensitive_flag_name(name: str) -> bool:
    lowered = name.lower().lstrip("-")
    return any(token in lowered for token in _SENSITIVE_FLAG_TOKENS)


def _format_dt(value: _dt.datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _parse_user_timestamp(value: str) -> _dt.datetime:
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1]
    value = value.replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        with contextlib.suppress(ValueError):
            return _dt.datetime.strptime(value, fmt)
    raise ValueError(f"unsupported timestamp format: {value!r}")


def _parse_optional_timestamp(value: str | None) -> _dt.datetime | None:
    if not value:
        return None
    with contextlib.suppress(ValueError):
        return _parse_user_timestamp(value)
    return None


class RequestBodyTooLarge(ValueError):
    """Raised when an HTTP request declares a body larger than server policy."""


def _read_json_body(
    handler: BaseHTTPRequestHandler,
    max_bytes: int = DEFAULT_MAX_BODY_BYTES,
) -> dict[str, Any]:
    length = _content_length(handler)
    if length > max_bytes:
        raise RequestBodyTooLarge(
            f"request body is {length} bytes; limit is {max_bytes} bytes"
        )
    raw = handler.rfile.read(length) if length else b"{}"
    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON body: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("JSON body must be an object")
    return payload


def _read_optional_json_body(
    handler: BaseHTTPRequestHandler,
    max_bytes: int = DEFAULT_MAX_BODY_BYTES,
) -> dict[str, Any]:
    length = _content_length(handler)
    if length > max_bytes:
        raise RequestBodyTooLarge(
            f"request body is {length} bytes; limit is {max_bytes} bytes"
        )
    raw = handler.rfile.read(length) if length else b"{}"
    with contextlib.suppress(UnicodeDecodeError, json.JSONDecodeError):
        payload = json.loads(raw.decode("utf-8"))
        if isinstance(payload, dict):
            return payload
    return {}


def _content_length(handler: BaseHTTPRequestHandler) -> int:
    value = handler.headers.get("content-length")
    if not value:
        return 0
    try:
        length = int(value)
    except ValueError as exc:
        raise ValueError("invalid content-length header") from exc
    if length < 0:
        raise ValueError("invalid negative content-length header")
    return length


def kubernetes_api_response(
    state: SimulationState,
    method: str,
    path: str,
    query: dict[str, list[str]],
    accept_header: str = "",
) -> KubernetesApiResponse | None:
    if path != "/version" and not path.startswith(("/api", "/apis")):
        return None
    if method != "GET":
        return _k8s_read_only_response(method, path)
    if path == "/version":
        return _k8s_json_response({
            "major": "1",
            "minor": "29",
            "gitVersion": "v1.29.4-amc",
            "gitCommit": "simulated",
            "gitTreeState": "clean",
            "buildDate": _k8s_timestamp(state.clock.now()),
            "goVersion": "go1.22.0",
            "compiler": "gc",
            "platform": "linux/amd64",
        }, "k8s.version")
    if path == "/api":
        return _k8s_json_response({
            "kind": "APIVersions",
            "apiVersion": "v1",
            "versions": ["v1"],
            "serverAddressByClientCIDRs": [],
        }, "k8s.discovery.core")
    if path == "/apis":
        return _k8s_json_response(_k8s_api_group_list(), "k8s.discovery.groups")

    parts = [part for part in path.strip("/").split("/") if part]
    if len(parts) == 2 and parts == ["api", "v1"]:
        return _k8s_json_response(_k8s_api_resource_list("", "v1"), "k8s.discovery.v1")
    if parts[:2] == ["api", "v1"]:
        return _k8s_core_resource_response(state, parts, query, _accepts_table(accept_header))
    if parts and parts[0] == "apis":
        return _k8s_group_resource_response(state, parts, query, _accepts_table(accept_header))
    return _k8s_status_response(
        404,
        f"{path} is not implemented by the simulator Kubernetes API",
        "NotFound",
        "unsupported",
        "k8s.path.unsupported",
    )


def kubernetes_api_post_response(
    state: SimulationState,
    path: str,
    payload: dict[str, Any],
) -> KubernetesApiResponse | None:
    if path == "/version":
        return _k8s_read_only_response("POST", path)
    if not path.startswith(("/api", "/apis")):
        return None
    if path == "/apis/authorization.k8s.io/v1/selfsubjectaccessreviews":
        return KubernetesApiResponse(
            201,
            {
                "kind": "SelfSubjectAccessReview",
                "apiVersion": "authorization.k8s.io/v1",
                "metadata": payload.get("metadata", {}),
                "spec": payload.get("spec", {}),
                "status": {
                    "allowed": True,
                    "reason": "AMC simulator permits read-only diagnostic commands.",
                },
            },
            "application/json; charset=utf-8",
            "supported",
            "k8s.authorization.selfsubjectaccessreviews.create",
        )
    return kubernetes_api_mutating_response(state, "POST", path, payload)


def kubernetes_api_mutating_response(
    state: SimulationState,
    method: str,
    path: str,
    payload: dict[str, Any],
) -> KubernetesApiResponse:
    target = _k8s_mutation_target(path)
    if target is None:
        return _k8s_status_response(
            *_k8s_read_only_status_args(method, path),
        )
    resource = target["resource"]
    name = target["name"]
    subresource = target["subresource"]
    now = state.clock.now()
    if method in {"PATCH", "PUT"} and resource == "deployments" and name:
        replicas = _payload_replicas(payload)
        if replicas is not None:
            state.mutations.set_workload(
                name,
                now=now,
                replicas=replicas,
                ready_replicas=replicas,
                deployment_status="Healthy" if replicas else "ScaledToZero",
                pod_status="Running",
            )
            reason = "ScalingReplicaSet" if subresource == "scale" else "Patched"
            state.mutations.record_event(
                "Normal",
                reason,
                f"deployment/{name}",
                f"{method.lower()} set deployment {name} replicas to {replicas}",
                now,
            )
        deployment = _find_named(resource_snapshot(state)["deployments"], name)
        if deployment is None:
            return _k8s_status_response(
                404,
                f"deployments {name!r} not found",
                "NotFound",
                "supported",
                "k8s.apps.deployments.mutate.not_found",
            )
        body = _k8s_scale(state, deployment) if subresource == "scale" else _k8s_deployment(state, deployment)
        return _k8s_json_response(body, f"k8s.apps.deployments.{method.lower()}")
    snapshot_kind = _mutation_snapshot_kind(resource)
    if method in {"PATCH", "PUT"} and snapshot_kind and name:
        state.mutations.put_resource(
            snapshot_kind,
            name,
            _generic_resource_row(state, snapshot_kind, name, payload=payload),
            now=now,
        )
        body = _k8s_mutated_object(state, target, snapshot_kind, name)
        if body is not None:
            return _k8s_json_response(body, f"k8s.{resource}.{method.lower()}")
        return _k8s_status_response(
            200,
            f"{resource} {name!r} configured by simulator",
            "Configured",
            "supported",
            f"k8s.{resource}.{method.lower()}",
        )
    if method == "DELETE" and resource == "pods" and name:
        state.mutations.delete_pod(name, now=now)
        return _k8s_status_response(
            200,
            f"pods {name!r} deleted",
            "Deleted",
            "supported",
            "k8s.core.pods.delete",
        )
    if method == "DELETE" and resource == "deployments" and name:
        state.mutations.set_workload(
            name,
            now=now,
            replicas=0,
            ready_replicas=0,
            deployment_status="Deleted",
            pod_status="Terminating",
            deleted=True,
        )
        state.mutations.record_event(
            "Normal",
            "Deleted",
            f"deployment/{name}",
            f"deployment {name} deleted from simulator state",
            now,
        )
        return _k8s_status_response(
            200,
            f"deployments {name!r} deleted",
            "Deleted",
            "supported",
            "k8s.apps.deployments.delete",
        )
    if method == "DELETE" and snapshot_kind and name:
        state.mutations.delete_resource(snapshot_kind, name, now=now)
        return _k8s_status_response(
            200,
            f"{resource} {name!r} deleted",
            "Deleted",
            "supported",
            f"k8s.{resource}.delete",
        )
    if method == "POST":
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        name = (
            name
            or str(metadata.get("name", ""))
            or f"simulated-{_normalized_resource_prefix(resource)}"
        )
        if snapshot_kind:
            state.mutations.put_resource(
                snapshot_kind,
                name,
                _generic_resource_row(state, snapshot_kind, name, payload=payload),
                now=now,
            )
            body = _k8s_mutated_object(state, target, snapshot_kind, name)
            if body is not None:
                return KubernetesApiResponse(
                    201,
                    body,
                    "application/json; charset=utf-8",
                    "supported",
                    f"k8s.{resource}.create",
                )
        state.mutations.record_event(
            "Normal",
            "Created",
            f"{resource}/{name}",
            f"accepted create request for {resource}",
            now,
        )
        return _k8s_status_response(
            201,
            f"{resource} create accepted by simulator",
            "Created",
            "partial",
            f"k8s.{resource}.create.partial",
        )
    return _k8s_status_response(
        *_k8s_read_only_status_args(method, path),
    )


def _k8s_mutation_target(path: str) -> dict[str, str] | None:
    parts = [part for part in path.strip("/").split("/") if part]
    if parts[:3] == ["api", "v1", "namespaces"] and len(parts) >= 5:
        return {
            "group": "",
            "version": "v1",
            "namespace": parts[3],
            "resource": parts[4],
            "name": parts[5] if len(parts) >= 6 else "",
            "subresource": parts[6] if len(parts) >= 7 else "",
        }
    if parts and parts[0] == "apis" and len(parts) >= 6 and parts[3] == "namespaces":
        return {
            "group": parts[1],
            "version": parts[2],
            "namespace": parts[4],
            "resource": parts[5],
            "name": parts[6] if len(parts) >= 7 else "",
            "subresource": parts[7] if len(parts) >= 8 else "",
        }
    return None


def _k8s_mutated_object(
    state: SimulationState,
    target: dict[str, str],
    snapshot_kind: str,
    name: str,
) -> dict[str, Any] | None:
    resource = target["resource"]
    group = target["group"]
    objects = _k8s_objects_for_resource(state, group, resource)
    if objects is None and snapshot_kind == "hpa":
        objects = _k8s_objects_for_resource(state, "autoscaling", "horizontalpodautoscalers")
    if objects is None and snapshot_kind == "ingress":
        objects = _k8s_objects_for_resource(state, "networking.k8s.io", "ingresses")
    if objects is None and snapshot_kind == "pvc":
        objects = _k8s_objects_for_resource(state, "", "persistentvolumeclaims")
    if objects is None:
        return None
    for obj in objects:
        if obj.get("metadata", {}).get("name") == name:
            return obj
    return None


def _payload_replicas(payload: dict[str, Any]) -> int | None:
    spec = payload.get("spec")
    if isinstance(spec, dict) and "replicas" in spec:
        if isinstance(spec["replicas"], bool):
            return None
        try:
            return max(0, int(spec["replicas"]))
        except (TypeError, ValueError):
            return None
    return None


def _k8s_scale(state: SimulationState, deployment: dict[str, Any]) -> dict[str, Any]:
    replicas = int(str(deployment["ready"]).split("/", 1)[1])
    ready = int(str(deployment["ready"]).split("/", 1)[0])
    return {
        "apiVersion": "autoscaling/v1",
        "kind": "Scale",
        "metadata": _k8s_metadata(state, deployment["name"], namespace=state.namespace),
        "spec": {"replicas": replicas},
        "status": {
            "replicas": replicas,
            "selector": f"app.kubernetes.io/name={deployment['name']}",
            "readyReplicas": ready,
        },
    }


def render_kubeconfig(
    server_url: str,
    namespace: str = DEFAULT_NAMESPACE,
    token: str = "",
) -> str:
    user_block = "  user: {}\n"
    if token:
        user_block = f"  user:\n    token: {json.dumps(token)}\n"
    return (
        "apiVersion: v1\n"
        "kind: Config\n"
        "clusters:\n"
        "- name: amc-simulator\n"
        "  cluster:\n"
        f"    server: {server_url}\n"
        "    insecure-skip-tls-verify: true\n"
        "contexts:\n"
        "- name: amc-simulator\n"
        "  context:\n"
        "    cluster: amc-simulator\n"
        "    user: amc-simulator\n"
        f"    namespace: {namespace}\n"
        "current-context: amc-simulator\n"
        "users:\n"
        "- name: amc-simulator\n"
        f"{user_block}"
    )


def record_kubernetes_api_call(
    state: SimulationState,
    *,
    method: str,
    path: str,
    query: dict[str, list[str]],
    response: KubernetesApiResponse,
    client: str,
    user_agent: str,
    latency_ms: float,
) -> None:
    trace_query = _redact_query(query)
    raw_input = method + " " + path
    if trace_query:
        raw_input += "?" + urllib.parse.urlencode(trace_query, doseq=True)
    stdout = _api_trace_body(response)
    trace = CommandTrace(
        id=state.traces.next_id(),
        received_at_wall_time=_dt.datetime.now(_dt.timezone.utc).isoformat(),
        simulated_time=_format_dt(state.clock.now()),
        raw_input=raw_input,
        argv=(method, path),
        client=client,
        command_family="kubernetes-api",
        verb=method,
        resource_kind=_api_resource_kind(path),
        resource_name=_api_resource_name(path),
        namespace=_api_namespace(path) or state.namespace,
        parsed_flags={
            "query": trace_query,
            "user_agent": user_agent,
        },
        support_status=response.support_status,
        matched_rule_id=response.matched_rule_id,
        active_scenarios=state.active_scenarios,
        exit_code=0 if response.status < 400 else 1,
        stdout_preview=_preview(stdout),
        stderr_preview="",
        stdout=stdout,
        stderr="",
        latency_ms=round(latency_ms, 3),
        fingerprint=_api_fingerprint(method, path),
        guessed_intent=_api_guess_intent(path, response),
    )
    state.traces.record(trace)


def _redact_query(query: dict[str, list[str]]) -> dict[str, list[str]]:
    return {
        key: ["***"] if key.lower() in _SENSITIVE_QUERY_KEYS else list(values)
        for key, values in query.items()
    }


def _k8s_json_response(body: dict[str, Any], matched_rule_id: str) -> KubernetesApiResponse:
    return KubernetesApiResponse(
        200,
        body,
        "application/json; charset=utf-8",
        "supported",
        matched_rule_id,
    )


def _k8s_text_response(text: str, matched_rule_id: str) -> KubernetesApiResponse:
    return KubernetesApiResponse(
        200,
        text,
        "text/plain; charset=utf-8",
        "supported",
        matched_rule_id,
    )


def _k8s_status_response(
    status: int,
    message: str,
    reason: str,
    support_status: str,
    matched_rule_id: str,
) -> KubernetesApiResponse:
    return KubernetesApiResponse(
        status,
        {
            "kind": "Status",
            "apiVersion": "v1",
            "metadata": {},
            "status": "Success" if status < 400 else "Failure",
            "message": message,
            "reason": reason,
            "code": status,
        },
        "application/json; charset=utf-8",
        support_status,
        matched_rule_id,
    )


def _k8s_read_only_response(method: str, path: str) -> KubernetesApiResponse:
    return _k8s_status_response(*_k8s_read_only_status_args(method, path))


def _k8s_read_only_status_args(method: str, path: str) -> tuple[int, str, str, str, str]:
    return (
        405,
        f"{method} {path} is not supported by the simulator Kubernetes mutation facade",
        "MethodNotAllowed",
        "unsupported",
        "k8s.method.unsupported",
    )


def _k8s_api_group_list() -> dict[str, Any]:
    groups = [
        _k8s_api_group("apps", "v1"),
        _k8s_api_group("autoscaling", "v2"),
        _k8s_api_group("authorization.k8s.io", "v1"),
        _k8s_api_group("batch", "v1"),
        _k8s_api_group("discovery.k8s.io", "v1"),
        _k8s_api_group("networking.k8s.io", "v1"),
        _k8s_api_group("metrics.k8s.io", "v1beta1"),
    ]
    return {"kind": "APIGroupList", "apiVersion": "v1", "groups": groups}


def _k8s_api_group(name: str, version: str) -> dict[str, Any]:
    return {
        "name": name,
        "versions": [{"groupVersion": f"{name}/{version}", "version": version}],
        "preferredVersion": {"groupVersion": f"{name}/{version}", "version": version},
    }


def _k8s_group_resource_response(
    state: SimulationState,
    parts: list[str],
    query: dict[str, list[str]],
    as_table: bool,
) -> KubernetesApiResponse:
    if len(parts) < 2:
        return _k8s_status_response(
            404, "/apis requires an API group", "NotFound", "unsupported", "k8s.apis.malformed"
        )
    group = parts[1]
    versions = {
        "apps": "v1",
        "autoscaling": "v2",
        "authorization.k8s.io": "v1",
        "batch": "v1",
        "discovery.k8s.io": "v1",
        "networking.k8s.io": "v1",
        "metrics.k8s.io": "v1beta1",
    }
    if group not in versions:
        return _k8s_status_response(
            404,
            f"API group {group!r} is not implemented by the simulator",
            "NotFound",
            "unsupported",
            "k8s.group.unsupported",
        )
    if len(parts) == 2:
        return _k8s_json_response(_k8s_api_group(group, versions[group]), f"k8s.discovery.{group}")
    version = parts[2]
    if version != versions[group]:
        return _k8s_status_response(
            404,
            f"API version {group}/{version} is not implemented by the simulator",
            "NotFound",
            "unsupported",
            "k8s.version.unsupported",
        )
    if len(parts) == 3:
        return _k8s_json_response(
            _k8s_api_resource_list(group, version),
            f"k8s.discovery.{group}.{version}",
        )
    if len(parts) >= 6 and parts[3] == "namespaces":
        namespace = parts[4]
        resource = parts[5]
        name = parts[6] if len(parts) >= 7 else ""
        subresource = parts[7] if len(parts) >= 8 else ""
        if group == "apps" and resource == "deployments" and name and subresource == "scale":
            deployment = _find_named(resource_snapshot(state)["deployments"], name)
            if deployment is None:
                return _k8s_status_response(
                    404,
                    f"{resource} {name!r} not found",
                    "NotFound",
                    "supported",
                    "k8s.apps.get.scale.not_found",
                )
            return _k8s_json_response(_k8s_scale(state, deployment), "k8s.apps.get.scale")
        return _k8s_resource_response(
            state, group, version, namespace, resource, name, query, as_table
        )
    if group == "metrics.k8s.io" and len(parts) >= 4 and parts[3] == "nodes":
        name = parts[4] if len(parts) >= 5 else ""
        return _k8s_resource_response(
            state, group, version, "", "nodes", name, query, as_table
        )
    return _k8s_status_response(
        404,
        f"/{'/'.join(parts)} is not implemented by the simulator Kubernetes API",
        "NotFound",
        "unsupported",
        "k8s.group.path.unsupported",
    )


def _k8s_core_resource_response(
    state: SimulationState,
    parts: list[str],
    query: dict[str, list[str]],
    as_table: bool,
) -> KubernetesApiResponse:
    if len(parts) == 3:
        return _k8s_resource_response(state, "", "v1", "", parts[2], "", query, as_table)
    if len(parts) == 4 and parts[2] in {"nodes", "namespaces"}:
        return _k8s_resource_response(
            state, "", "v1", "", parts[2], parts[3], query, as_table
        )
    if len(parts) >= 5 and parts[2] == "namespaces":
        namespace = parts[3]
        if len(parts) == 4:
            return _k8s_resource_response(
                state, "", "v1", "", "namespaces", namespace, query, as_table
            )
        resource = parts[4]
        name = parts[5] if len(parts) >= 6 else ""
        if resource == "pods" and len(parts) >= 7 and parts[6] == "log":
            pod_name = name
            parsed = ParsedCommand(
                raw_input=f"kubectl logs {pod_name} -n {namespace}",
                argv=("kubectl", "logs", pod_name, "-n", namespace),
                family="kubectl",
                verb="logs",
                resource_kind="pods",
                resource_name=pod_name,
                namespace=namespace,
                flags={"namespace": namespace},
                positionals=("logs", pod_name),
            )
            return _k8s_text_response(_render_logs(state, parsed), "k8s.core.pods.log")
        return _k8s_resource_response(
            state, "", "v1", namespace, resource, name, query, as_table
        )
    return _k8s_status_response(
        404,
        f"/{'/'.join(parts)} is not implemented by the simulator Kubernetes API",
        "NotFound",
        "unsupported",
        "k8s.core.path.unsupported",
    )


def _k8s_api_resource_list(group: str, version: str) -> dict[str, Any]:
    read_verbs = ["get", "list"]
    mutate_verbs = ["create", "delete", "get", "list", "patch", "update"]
    resources_by_group = {
        "": [
            ("namespaces", "Namespace", False, read_verbs),
            ("nodes", "Node", False, read_verbs),
            ("pods", "Pod", True, ["get", "list", "delete"]),
            ("pods/log", "Pod", True, ["get"]),
            ("configmaps", "ConfigMap", True, mutate_verbs),
            ("secrets", "Secret", True, mutate_verbs),
            ("replicationcontrollers", "ReplicationController", True, read_verbs),
            ("services", "Service", True, mutate_verbs),
            ("endpoints", "Endpoints", True, read_verbs),
            ("events", "Event", True, read_verbs),
            ("persistentvolumeclaims", "PersistentVolumeClaim", True, mutate_verbs),
            ("serviceaccounts", "ServiceAccount", True, mutate_verbs),
        ],
        "apps": [
            ("deployments", "Deployment", True, mutate_verbs),
            ("deployments/scale", "Scale", True, ["get", "patch", "update"]),
            ("replicasets", "ReplicaSet", True, read_verbs),
            ("daemonsets", "DaemonSet", True, mutate_verbs),
            ("statefulsets", "StatefulSet", True, mutate_verbs),
        ],
        "autoscaling": [
            ("horizontalpodautoscalers", "HorizontalPodAutoscaler", True, mutate_verbs),
        ],
        "authorization.k8s.io": [
            ("selfsubjectaccessreviews", "SelfSubjectAccessReview", False, ["create"]),
        ],
        "batch": [
            ("jobs", "Job", True, mutate_verbs),
            ("cronjobs", "CronJob", True, mutate_verbs),
        ],
        "discovery.k8s.io": [
            ("endpointslices", "EndpointSlice", True, read_verbs),
        ],
        "networking.k8s.io": [
            ("ingresses", "Ingress", True, mutate_verbs),
        ],
        "metrics.k8s.io": [
            ("nodes", "NodeMetrics", False, read_verbs),
            ("pods", "PodMetrics", True, read_verbs),
        ],
    }
    group_version = version if not group else f"{group}/{version}"
    resources = []
    for name, kind, namespaced, verbs in resources_by_group.get(group, []):
        entry = {
            "name": name,
            "singularName": "",
            "namespaced": namespaced,
            "kind": kind,
            "verbs": verbs,
        }
        if name == "pods":
            entry["shortNames"] = ["po"]
        elif name == "configmaps":
            entry["shortNames"] = ["cm"]
        elif name == "services":
            entry["shortNames"] = ["svc"]
        elif name == "endpoints":
            entry["shortNames"] = ["ep"]
        elif name == "serviceaccounts":
            entry["shortNames"] = ["sa"]
        elif name == "replicationcontrollers":
            entry["shortNames"] = ["rc"]
        elif name == "persistentvolumeclaims":
            entry["shortNames"] = ["pvc"]
        elif name == "deployments":
            entry["shortNames"] = ["deploy"]
        elif name == "replicasets":
            entry["shortNames"] = ["rs"]
        elif name == "daemonsets":
            entry["shortNames"] = ["ds"]
        elif name == "statefulsets":
            entry["shortNames"] = ["sts"]
        elif name == "horizontalpodautoscalers":
            entry["shortNames"] = ["hpa"]
        elif name == "cronjobs":
            entry["shortNames"] = ["cj"]
        elif name == "ingresses":
            entry["shortNames"] = ["ing"]
        if name in {
            "pods",
            "services",
            "deployments",
            "replicasets",
            "daemonsets",
            "statefulsets",
            "horizontalpodautoscalers",
            "jobs",
            "cronjobs",
        }:
            entry["categories"] = ["all"]
        resources.append(entry)
    return {
        "kind": "APIResourceList",
        "apiVersion": "v1",
        "groupVersion": group_version,
        "resources": resources,
    }


def _k8s_resource_response(
    state: SimulationState,
    group: str,
    version: str,
    namespace: str,
    resource: str,
    name: str,
    query: dict[str, list[str]],
    as_table: bool,
) -> KubernetesApiResponse:
    objects = _k8s_objects_for_resource(state, group, resource)
    if objects is None:
        return _k8s_status_response(
            404,
            f"resource {resource!r} is not implemented by the simulator Kubernetes API",
            "NotFound",
            "unsupported",
            "k8s.resource.unsupported",
        )
    if namespace and namespace != state.namespace:
        objects = []
    objects = _filter_k8s_objects(objects, query)
    meta = _k8s_resource_meta(group, version, resource)
    if name:
        for obj in objects:
            if obj.get("metadata", {}).get("name") == name:
                if as_table:
                    return _k8s_json_response(
                        _k8s_table(resource, [obj]),
                        f"k8s.{group or 'core'}.get.{resource}.table",
                    )
                return _k8s_json_response(obj, f"k8s.{group or 'core'}.get.{resource}")
        return _k8s_status_response(
            404,
            f"{resource} {name!r} not found",
            "NotFound",
            "supported",
            f"k8s.{group or 'core'}.get.not_found",
        )
    if as_table:
        return _k8s_json_response(
            _k8s_table(resource, objects),
            f"k8s.{group or 'core'}.list.{resource}.table",
        )
    return _k8s_json_response({
        "kind": meta["list_kind"],
        "apiVersion": meta["api_version"],
        "metadata": {"resourceVersion": "1"},
        "items": objects,
    }, f"k8s.{group or 'core'}.list.{resource}")


def _k8s_resource_meta(group: str, version: str, resource: str) -> dict[str, str]:
    api_version = version if not group else f"{group}/{version}"
    kinds = {
        "namespaces": "Namespace",
        "nodes": "Node" if group != "metrics.k8s.io" else "NodeMetrics",
        "pods": "Pod" if group != "metrics.k8s.io" else "PodMetrics",
        "configmaps": "ConfigMap",
        "secrets": "Secret",
        "replicationcontrollers": "ReplicationController",
        "services": "Service",
        "endpoints": "Endpoints",
        "endpointslices": "EndpointSlice",
        "events": "Event",
        "persistentvolumeclaims": "PersistentVolumeClaim",
        "serviceaccounts": "ServiceAccount",
        "deployments": "Deployment",
        "replicasets": "ReplicaSet",
        "daemonsets": "DaemonSet",
        "statefulsets": "StatefulSet",
        "horizontalpodautoscalers": "HorizontalPodAutoscaler",
        "jobs": "Job",
        "cronjobs": "CronJob",
        "ingresses": "Ingress",
    }
    kind = kinds.get(resource, resource.rstrip("s").title())
    return {"api_version": api_version, "kind": kind, "list_kind": f"{kind}List"}


def _accepts_table(accept_header: str) -> bool:
    return "as=Table" in accept_header and "g=meta.k8s.io" in accept_header


def _k8s_table(resource: str, objects: list[dict[str, Any]]) -> dict[str, Any]:
    columns, cell_builder = _k8s_table_schema(resource)
    return {
        "kind": "Table",
        "apiVersion": "meta.k8s.io/v1",
        "metadata": {"resourceVersion": "1"},
        "columnDefinitions": columns,
        "rows": [
            {
                "cells": cell_builder(obj),
                "object": {
                    "kind": "PartialObjectMetadata",
                    "apiVersion": "meta.k8s.io/v1",
                    "metadata": obj.get("metadata", {}),
                },
            }
            for obj in objects
        ],
    }


def _k8s_column(name: str, column_type: str = "string") -> dict[str, str]:
    return {
        "name": name,
        "type": column_type,
        "format": "name" if name == "Name" else "",
        "description": name,
    }


def _k8s_table_schema(resource: str):
    if resource == "pods":
        return (
            [
                _k8s_column("Name"),
                _k8s_column("Ready"),
                _k8s_column("Status"),
                _k8s_column("Restarts", "integer"),
                _k8s_column("Age"),
            ],
            _k8s_pod_cells,
        )
    if resource == "deployments":
        return (
            [
                _k8s_column("Name"),
                _k8s_column("Ready"),
                _k8s_column("Up-to-date", "integer"),
                _k8s_column("Available", "integer"),
                _k8s_column("Age"),
            ],
            _k8s_deployment_cells,
        )
    if resource == "services":
        return (
            [
                _k8s_column("Name"),
                _k8s_column("Type"),
                _k8s_column("Cluster-IP"),
                _k8s_column("External-IP"),
                _k8s_column("Port(s)"),
                _k8s_column("Age"),
            ],
            _k8s_service_cells,
        )
    if resource == "endpoints":
        return (
            [_k8s_column("Name"), _k8s_column("Endpoints"), _k8s_column("Age")],
            _k8s_endpoints_cells,
        )
    if resource == "endpointslices":
        return (
            [
                _k8s_column("Name"),
                _k8s_column("AddressType"),
                _k8s_column("Ports"),
                _k8s_column("Endpoints", "integer"),
                _k8s_column("Age"),
            ],
            _k8s_endpointslice_cells,
        )
    if resource == "events":
        return (
            [
                _k8s_column("Last Seen"),
                _k8s_column("Type"),
                _k8s_column("Reason"),
                _k8s_column("Object"),
                _k8s_column("Message"),
            ],
            _k8s_event_cells,
        )
    if resource == "horizontalpodautoscalers":
        return (
            [
                _k8s_column("Name"),
                _k8s_column("Reference"),
                _k8s_column("Targets"),
                _k8s_column("Minpods", "integer"),
                _k8s_column("Maxpods", "integer"),
                _k8s_column("Replicas", "integer"),
                _k8s_column("Age"),
            ],
            _k8s_hpa_cells,
        )
    if resource == "nodes":
        return (
            [
                _k8s_column("Name"),
                _k8s_column("Status"),
                _k8s_column("Roles"),
                _k8s_column("Age"),
                _k8s_column("Version"),
            ],
            _k8s_node_cells,
        )
    if resource == "replicasets":
        return (
            [
                _k8s_column("Name"),
                _k8s_column("Desired", "integer"),
                _k8s_column("Current", "integer"),
                _k8s_column("Ready", "integer"),
                _k8s_column("Age"),
            ],
            _k8s_replicaset_cells,
        )
    if resource == "daemonsets":
        return (
            [
                _k8s_column("Name"),
                _k8s_column("Desired", "integer"),
                _k8s_column("Current", "integer"),
                _k8s_column("Ready", "integer"),
                _k8s_column("Up-to-date", "integer"),
                _k8s_column("Available", "integer"),
                _k8s_column("Age"),
            ],
            _k8s_daemonset_cells,
        )
    if resource == "persistentvolumeclaims":
        return (
            [
                _k8s_column("Name"),
                _k8s_column("Status"),
                _k8s_column("Volume"),
                _k8s_column("Capacity"),
                _k8s_column("Access Modes"),
                _k8s_column("Storageclass"),
                _k8s_column("Age"),
            ],
            _k8s_pvc_cells,
        )
    if resource == "statefulsets":
        return (
            [_k8s_column("Name"), _k8s_column("Ready"), _k8s_column("Age")],
            _k8s_statefulset_cells,
        )
    if resource == "ingresses":
        return (
            [
                _k8s_column("Name"),
                _k8s_column("Class"),
                _k8s_column("Hosts"),
                _k8s_column("Address"),
                _k8s_column("Ports"),
                _k8s_column("Age"),
            ],
            _k8s_ingress_cells,
        )
    if resource == "secrets":
        return (
            [
                _k8s_column("Name"),
                _k8s_column("Type"),
                _k8s_column("Data", "integer"),
                _k8s_column("Age"),
            ],
            _k8s_secret_cells,
        )
    if resource == "configmaps":
        return (
            [_k8s_column("Name"), _k8s_column("Data", "integer"), _k8s_column("Age")],
            _k8s_configmap_cells,
        )
    if resource == "serviceaccounts":
        return (
            [_k8s_column("Name"), _k8s_column("Secrets", "integer"), _k8s_column("Age")],
            _k8s_serviceaccount_cells,
        )
    if resource == "jobs":
        return (
            [
                _k8s_column("Name"),
                _k8s_column("Completions"),
                _k8s_column("Duration"),
                _k8s_column("Age"),
            ],
            _k8s_job_cells,
        )
    if resource == "cronjobs":
        return (
            [
                _k8s_column("Name"),
                _k8s_column("Schedule"),
                _k8s_column("Suspend"),
                _k8s_column("Active", "integer"),
                _k8s_column("Last Schedule"),
                _k8s_column("Age"),
            ],
            _k8s_cronjob_cells,
        )
    if resource == "namespaces":
        return (
            [_k8s_column("Name"), _k8s_column("Status"), _k8s_column("Age")],
            _k8s_namespace_cells,
        )
    return ([_k8s_column("Name"), _k8s_column("Age")], _k8s_default_cells)


def _k8s_pod_cells(obj: dict[str, Any]) -> list[Any]:
    statuses = obj.get("status", {}).get("containerStatuses", [])
    ready = sum(1 for status in statuses if status.get("ready"))
    restarts = sum(int(status.get("restartCount", 0)) for status in statuses)
    return [
        obj["metadata"]["name"],
        f"{ready}/{len(statuses) or 1}",
        _k8s_pod_display_status(obj),
        restarts,
        "7d",
    ]


def _k8s_pod_display_status(obj: dict[str, Any]) -> str:
    statuses = obj.get("status", {}).get("containerStatuses", [])
    for status in statuses:
        state = status.get("state", {})
        if "waiting" in state:
            return state["waiting"].get("reason", "Waiting")
        if "terminated" in state:
            return state["terminated"].get("reason", "Terminated")
    return obj.get("status", {}).get("phase", "Unknown")


def _k8s_deployment_cells(obj: dict[str, Any]) -> list[Any]:
    spec = obj.get("spec", {})
    status = obj.get("status", {})
    replicas = int(spec.get("replicas", 0))
    ready = int(status.get("readyReplicas", 0))
    return [
        obj["metadata"]["name"],
        f"{ready}/{replicas}",
        int(status.get("updatedReplicas", 0)),
        int(status.get("availableReplicas", 0)),
        "7d",
    ]


def _k8s_service_cells(obj: dict[str, Any]) -> list[Any]:
    spec = obj.get("spec", {})
    ports = ",".join(
        f"{port.get('port')}/{port.get('protocol', 'TCP')}"
        for port in spec.get("ports", [])
    )
    return [
        obj["metadata"]["name"],
        spec.get("type", "ClusterIP"),
        spec.get("clusterIP", "<none>"),
        "<none>",
        ports,
        "7d",
    ]


def _k8s_endpoints_cells(obj: dict[str, Any]) -> list[Any]:
    subsets = obj.get("subsets", [])
    endpoints = []
    for subset in subsets:
        ports = subset.get("ports", [])
        port = ports[0].get("port", 8080) if ports else 8080
        for address in subset.get("addresses", []):
            endpoints.append(f"{address.get('ip')}:{port}")
    return [obj["metadata"]["name"], ",".join(endpoints) or "<none>", "7d"]


def _k8s_endpointslice_cells(obj: dict[str, Any]) -> list[Any]:
    ports = ",".join(str(port.get("port", "")) for port in obj.get("ports", []))
    return [
        obj["metadata"]["name"],
        obj.get("addressType", "IPv4"),
        ports,
        len(obj.get("endpoints", [])),
        "7d",
    ]


def _k8s_event_cells(obj: dict[str, Any]) -> list[Any]:
    involved = obj.get("involvedObject", {})
    return [
        "0s",
        obj.get("type", ""),
        obj.get("reason", ""),
        f"{involved.get('kind', '').lower()}/{involved.get('name', '')}",
        obj.get("message", ""),
    ]


def _k8s_hpa_cells(obj: dict[str, Any]) -> list[Any]:
    spec = obj.get("spec", {})
    status = obj.get("status", {})
    target = spec.get("scaleTargetRef", {})
    current = status.get("currentMetrics", [{}])[0].get("resource", {}).get("current", {})
    desired = spec.get("metrics", [{}])[0].get("resource", {}).get("target", {})
    current_pct = current.get("averageUtilization", 0)
    desired_pct = desired.get("averageUtilization", 0)
    return [
        obj["metadata"]["name"],
        f"{target.get('kind', 'Deployment')}/{target.get('name', '')}",
        f"{current_pct}%/{desired_pct}%",
        int(spec.get("minReplicas", 0)),
        int(spec.get("maxReplicas", 0)),
        int(status.get("currentReplicas", 0)),
        "7d",
    ]


def _k8s_node_cells(obj: dict[str, Any]) -> list[Any]:
    conditions = obj.get("status", {}).get("conditions", [])
    ready = next((condition for condition in conditions if condition.get("type") == "Ready"), {})
    role = obj.get("metadata", {}).get("labels", {}).get("kubernetes.io/role", "worker")
    version = obj.get("status", {}).get("nodeInfo", {}).get("kubeletVersion", "")
    return [
        obj["metadata"]["name"],
        "Ready" if ready.get("status") == "True" else "NotReady",
        role,
        "30d",
        version,
    ]


def _k8s_replicaset_cells(obj: dict[str, Any]) -> list[Any]:
    status = obj.get("status", {})
    return [
        obj["metadata"]["name"],
        int(status.get("replicas", 0)),
        int(status.get("fullyLabeledReplicas", status.get("replicas", 0))),
        int(status.get("readyReplicas", 0)),
        "7d",
    ]


def _k8s_daemonset_cells(obj: dict[str, Any]) -> list[Any]:
    status = obj.get("status", {})
    return [
        obj["metadata"]["name"],
        int(status.get("desiredNumberScheduled", 0)),
        int(status.get("currentNumberScheduled", 0)),
        int(status.get("numberReady", 0)),
        int(status.get("updatedNumberScheduled", 0)),
        int(status.get("numberAvailable", 0)),
        "7d",
    ]


def _k8s_pvc_cells(obj: dict[str, Any]) -> list[Any]:
    spec = obj.get("spec", {})
    status = obj.get("status", {})
    return [
        obj["metadata"]["name"],
        status.get("phase", ""),
        spec.get("volumeName", ""),
        status.get("capacity", {}).get("storage", ""),
        ",".join(status.get("accessModes", [])),
        spec.get("storageClassName", ""),
        "7d",
    ]


def _k8s_statefulset_cells(obj: dict[str, Any]) -> list[Any]:
    status = obj.get("status", {})
    return [
        obj["metadata"]["name"],
        f"{int(status.get('readyReplicas', 0))}/{int(status.get('replicas', 0))}",
        "7d",
    ]


def _k8s_ingress_cells(obj: dict[str, Any]) -> list[Any]:
    spec = obj.get("spec", {})
    status = obj.get("status", {})
    rules = spec.get("rules", [])
    ingress = status.get("loadBalancer", {}).get("ingress", [])
    return [
        obj["metadata"]["name"],
        spec.get("ingressClassName", ""),
        ",".join(rule.get("host", "") for rule in rules),
        ",".join(item.get("ip", "") for item in ingress),
        "80,443",
        "7d",
    ]


def _k8s_secret_cells(obj: dict[str, Any]) -> list[Any]:
    return [
        obj["metadata"]["name"],
        obj.get("type", "Opaque"),
        len(obj.get("data", {})),
        "7d",
    ]


def _k8s_configmap_cells(obj: dict[str, Any]) -> list[Any]:
    return [obj["metadata"]["name"], len(obj.get("data", {})), "7d"]


def _k8s_serviceaccount_cells(obj: dict[str, Any]) -> list[Any]:
    return [obj["metadata"]["name"], len(obj.get("secrets", [])), "7d"]


def _k8s_job_cells(obj: dict[str, Any]) -> list[Any]:
    status = obj.get("status", {})
    succeeded = int(status.get("succeeded", 0))
    completions = int(obj.get("spec", {}).get("completions", 1))
    return [obj["metadata"]["name"], f"{succeeded}/{completions}", "2m14s", "6d"]


def _k8s_cronjob_cells(obj: dict[str, Any]) -> list[Any]:
    spec = obj.get("spec", {})
    status = obj.get("status", {})
    return [
        obj["metadata"]["name"],
        spec.get("schedule", ""),
        str(spec.get("suspend", False)),
        len(status.get("active", [])),
        "18h",
        "7d",
    ]


def _k8s_namespace_cells(obj: dict[str, Any]) -> list[Any]:
    return [
        obj["metadata"]["name"],
        obj.get("status", {}).get("phase", "Active"),
        "7d",
    ]


def _k8s_default_cells(obj: dict[str, Any]) -> list[Any]:
    return [obj.get("metadata", {}).get("name", ""), "7d"]


def _k8s_objects_for_resource(
    state: SimulationState,
    group: str,
    resource: str,
) -> list[dict[str, Any]] | None:
    snapshot = resource_snapshot(state)
    if group == "metrics.k8s.io":
        if resource == "pods":
            return [_k8s_pod_metrics(state, pod) for pod in snapshot["pods"]]
        if resource == "nodes":
            return [_k8s_node_metrics(state, node) for node in snapshot["nodes"]]
        return None
    if resource == "namespaces":
        return [_k8s_namespace(state)]
    if resource == "nodes":
        return [_k8s_node(state, node) for node in snapshot["nodes"]]
    if resource == "pods":
        return [_k8s_pod(state, pod) for pod in snapshot["pods"]]
    if resource == "configmaps":
        return [_k8s_configmap(state, configmap) for configmap in snapshot["configmaps"]]
    if resource == "serviceaccounts":
        return [_k8s_serviceaccount(state, serviceaccount) for serviceaccount in snapshot["serviceaccounts"]]
    if resource == "replicationcontrollers":
        return []
    if resource == "services":
        return [_k8s_service(state, service) for service in snapshot["services"]]
    if resource == "endpoints":
        return [_k8s_endpoints(state, endpoint) for endpoint in snapshot["endpoints"]]
    if resource == "events":
        return [_k8s_event(state, event, index) for index, event in enumerate(snapshot["events"], start=1)]
    if resource == "persistentvolumeclaims":
        return [_k8s_pvc(state, pvc) for pvc in snapshot["pvc"]]
    if resource == "secrets":
        generic_secrets = [
            _k8s_secret(state, secret)
            for secret in snapshot["secrets"]
            if secret.get("type") != "helm.sh/release.v1"
        ]
        return [*_helm_secret_objects(state), *generic_secrets]
    if resource == "deployments" and group == "apps":
        return [_k8s_deployment(state, deployment) for deployment in snapshot["deployments"]]
    if resource == "replicasets" and group == "apps":
        return [_k8s_replicaset(state, replicaset) for replicaset in snapshot["replicasets"]]
    if resource == "daemonsets" and group == "apps":
        return [_k8s_daemonset(state, daemonset) for daemonset in snapshot["daemonsets"]]
    if resource == "statefulsets" and group == "apps":
        return [_k8s_statefulset(state, sts) for sts in snapshot["statefulsets"]]
    if resource == "horizontalpodautoscalers" and group == "autoscaling":
        return [_k8s_hpa(state, hpa) for hpa in snapshot["hpa"]]
    if resource == "jobs" and group == "batch":
        return [_k8s_job(state, job) for job in snapshot["jobs"]]
    if resource == "cronjobs" and group == "batch":
        return [_k8s_cronjob(state, cronjob) for cronjob in snapshot["cronjobs"]]
    if resource == "endpointslices" and group == "discovery.k8s.io":
        return [_k8s_endpointslice(state, endpointslice) for endpointslice in snapshot["endpointslices"]]
    if resource == "ingresses" and group == "networking.k8s.io":
        return [_k8s_ingress(state, ingress) for ingress in snapshot["ingress"]]
    return None


def _k8s_namespace(state: SimulationState) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": _k8s_metadata(state, state.namespace),
        "status": {"phase": "Active"},
    }


def _k8s_pod(state: SimulationState, pod: dict[str, Any]) -> dict[str, Any]:
    ready = pod["ready"].split("/")[0] == pod["ready"].split("/")[1]
    status_text = pod["status"]
    phase = "Failed" if status_text == "Error" else "Running"
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": _k8s_metadata(
            state,
            pod["name"],
            namespace=state.namespace,
            labels=_k8s_workload_labels(pod["component"]),
        ),
        "spec": {
            "nodeName": pod["node"],
            "containers": [{
                "name": pod["component"],
                "image": f"simulated-saas/{pod['component']}:0.3.0",
                "ports": [{"containerPort": 8080, "protocol": "TCP"}],
            }],
        },
        "status": {
            "phase": phase,
            "podIP": _stable_pod_ip(pod["name"]),
            "hostIP": "10.0.0.10",
            "startTime": _k8s_timestamp(state.clock.start_time),
            "conditions": [
                {"type": "Initialized", "status": "True"},
                {"type": "Ready", "status": "True" if ready else "False"},
                {"type": "ContainersReady", "status": "True" if ready else "False"},
                {"type": "PodScheduled", "status": "True"},
            ],
            "containerStatuses": [{
                "name": pod["component"],
                "ready": ready,
                "restartCount": pod["restarts"],
                "image": f"simulated-saas/{pod['component']}:0.3.0",
                "imageID": f"simulated-saas/{pod['component']}@sha256:simulated",
                "state": _k8s_container_state(state, status_text),
            }],
        },
    }


def _k8s_configmap(state: SimulationState, configmap: dict[str, Any]) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": _k8s_metadata(state, configmap["name"], namespace=state.namespace),
        "data": configmap["keys"],
    }


def _k8s_secret(state: SimulationState, secret: dict[str, Any]) -> dict[str, Any]:
    data_count = int(secret.get("data", 0) or 0)
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": _k8s_metadata(state, secret["name"], namespace=state.namespace),
        "type": secret.get("type", "Opaque"),
        "data": {f"key{index}": "c2ltdWxhdGVk" for index in range(max(1, data_count))},
    }


def _k8s_serviceaccount(state: SimulationState, serviceaccount: dict[str, Any]) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "ServiceAccount",
        "metadata": _k8s_metadata(state, serviceaccount["name"], namespace=state.namespace),
        "secrets": [],
    }


def _k8s_deployment(state: SimulationState, deployment: dict[str, Any]) -> dict[str, Any]:
    replicas = int(str(deployment["ready"]).split("/", 1)[1])
    ready_replicas = int(str(deployment["ready"]).split("/", 1)[0])
    name = deployment["name"]
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": _k8s_metadata(state, name, namespace=state.namespace, labels=_k8s_workload_labels(name)),
        "spec": {
            "replicas": replicas,
            "selector": {"matchLabels": {"app.kubernetes.io/name": name}},
            "template": {
                "metadata": {"labels": _k8s_workload_labels(name)},
                "spec": {"containers": [{"name": name, "image": f"simulated-saas/{name}:0.3.0"}]},
            },
        },
        "status": {
            "replicas": replicas,
            "readyReplicas": ready_replicas,
            "updatedReplicas": deployment["up_to_date"],
            "availableReplicas": deployment["available"],
            "conditions": [{
                "type": "Available",
                "status": "True" if ready_replicas else "False",
                "reason": deployment["status"],
                "message": f"deployment is {deployment['status']}",
            }],
        },
    }


def _k8s_replicaset(state: SimulationState, replicaset: dict[str, Any]) -> dict[str, Any]:
    owner = replicaset["owner"]
    return {
        "apiVersion": "apps/v1",
        "kind": "ReplicaSet",
        "metadata": _k8s_metadata(
            state,
            replicaset["name"],
            namespace=state.namespace,
            labels=_k8s_workload_labels(owner),
        ),
        "spec": {
            "replicas": replicaset["desired"],
            "selector": {"matchLabels": {"app.kubernetes.io/name": owner}},
        },
        "status": {
            "replicas": replicaset["current"],
            "fullyLabeledReplicas": replicaset["current"],
            "readyReplicas": replicaset["ready"],
            "availableReplicas": replicaset["ready"],
        },
    }


def _k8s_daemonset(state: SimulationState, daemonset: dict[str, Any]) -> dict[str, Any]:
    return {
        "apiVersion": "apps/v1",
        "kind": "DaemonSet",
        "metadata": _k8s_metadata(state, daemonset["name"], namespace=state.namespace),
        "spec": {
            "selector": {"matchLabels": {"app.kubernetes.io/name": daemonset["name"]}},
            "template": {
                "metadata": {"labels": {"app.kubernetes.io/name": daemonset["name"]}},
                "spec": {"containers": [{"name": daemonset["name"], "image": "simulated-saas/agent:0.3.0"}]},
            },
        },
        "status": {
            "desiredNumberScheduled": daemonset["desired"],
            "currentNumberScheduled": daemonset["current"],
            "numberReady": daemonset["ready"],
            "updatedNumberScheduled": daemonset["up_to_date"],
            "numberAvailable": daemonset["available"],
        },
    }


def _k8s_statefulset(state: SimulationState, sts: dict[str, Any]) -> dict[str, Any]:
    replicas = int(str(sts["ready"]).split("/", 1)[1])
    ready_replicas = int(str(sts["ready"]).split("/", 1)[0])
    name = sts["name"]
    return {
        "apiVersion": "apps/v1",
        "kind": "StatefulSet",
        "metadata": _k8s_metadata(state, name, namespace=state.namespace, labels=_k8s_workload_labels(name)),
        "spec": {
            "replicas": replicas,
            "serviceName": name,
            "selector": {"matchLabels": {"app.kubernetes.io/name": name}},
        },
        "status": {"replicas": replicas, "readyReplicas": ready_replicas},
    }


def _k8s_service(state: SimulationState, service: dict[str, Any]) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": _k8s_metadata(
            state,
            service["name"],
            namespace=state.namespace,
            labels=_k8s_workload_labels(service["name"]),
        ),
        "spec": {
            "type": service["type"],
            "clusterIP": service["cluster_ip"],
            "ports": [{"name": "http", "port": 8080, "protocol": "TCP", "targetPort": 8080}],
            "selector": {"app.kubernetes.io/name": service["name"]},
        },
    }


def _k8s_endpoints(state: SimulationState, endpoint: dict[str, Any]) -> dict[str, Any]:
    addresses = []
    for item in endpoint["endpoints"].split(","):
        ip, _, _port = item.partition(":")
        if ip:
            addresses.append({"ip": ip})
    return {
        "apiVersion": "v1",
        "kind": "Endpoints",
        "metadata": _k8s_metadata(state, endpoint["name"], namespace=state.namespace),
        "subsets": [{
            "addresses": addresses,
            "ports": [{"name": "http", "port": 8080, "protocol": "TCP"}],
        }],
    }


def _k8s_event(state: SimulationState, event: dict[str, str], index: int) -> dict[str, Any]:
    involved_kind, _, involved_name = event["object"].partition("/")
    return {
        "apiVersion": "v1",
        "kind": "Event",
        "metadata": _k8s_metadata(
            state,
            f"{involved_name}.{index}",
            namespace=state.namespace,
        ),
        "involvedObject": {
            "kind": involved_kind.title() if involved_kind else "Pod",
            "namespace": state.namespace,
            "name": involved_name or event["object"],
        },
        "reason": event["reason"],
        "message": event["message"],
        "type": event["type"],
        "count": 1,
        "firstTimestamp": _k8s_timestamp(state.clock.now()),
        "lastTimestamp": _k8s_timestamp(state.clock.now()),
        "source": {"component": "amc-simulator"},
    }


def _k8s_hpa(state: SimulationState, hpa: dict[str, Any]) -> dict[str, Any]:
    return {
        "apiVersion": "autoscaling/v2",
        "kind": "HorizontalPodAutoscaler",
        "metadata": _k8s_metadata(state, hpa["name"], namespace=state.namespace),
        "spec": {
            "scaleTargetRef": {"apiVersion": "apps/v1", "kind": "Deployment", "name": hpa["name"]},
            "minReplicas": hpa["minpods"],
            "maxReplicas": hpa["maxpods"],
            "metrics": [{
                "type": "Resource",
                "resource": {
                    "name": "cpu",
                    "target": {"type": "Utilization", "averageUtilization": 80},
                },
            }],
        },
        "status": {
            "currentReplicas": hpa["replicas"],
            "desiredReplicas": hpa["replicas"],
            "currentMetrics": [{
                "type": "Resource",
                "resource": {
                    "name": "cpu",
                    "current": {"averageUtilization": int(str(hpa["targets"]).split("%", 1)[0])},
                },
            }],
        },
    }


def _k8s_job(state: SimulationState, job: dict[str, Any]) -> dict[str, Any]:
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": _k8s_metadata(state, job["name"], namespace=state.namespace),
        "spec": {"completions": 1, "parallelism": 1},
        "status": {"succeeded": 1, "ready": 0},
    }


def _k8s_cronjob(state: SimulationState, cronjob: dict[str, Any]) -> dict[str, Any]:
    return {
        "apiVersion": "batch/v1",
        "kind": "CronJob",
        "metadata": _k8s_metadata(state, cronjob["name"], namespace=state.namespace),
        "spec": {
            "schedule": cronjob["schedule"],
            "suspend": cronjob["suspend"] == "True",
            "jobTemplate": {"spec": {"template": {"spec": {"restartPolicy": "OnFailure"}}}},
        },
        "status": {"active": [], "lastScheduleTime": _k8s_timestamp(state.clock.now())},
    }


def _k8s_pvc(state: SimulationState, pvc: dict[str, Any]) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": _k8s_metadata(state, pvc["name"], namespace=state.namespace),
        "spec": {
            "accessModes": [pvc["access_modes"]],
            "resources": {"requests": {"storage": pvc["capacity"]}},
            "storageClassName": pvc["storageclass"],
            "volumeName": pvc["volume"],
        },
        "status": {
            "phase": pvc["status"],
            "accessModes": [pvc["access_modes"]],
            "capacity": {"storage": pvc["capacity"]},
        },
    }


def _k8s_ingress(state: SimulationState, ingress: dict[str, Any]) -> dict[str, Any]:
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "Ingress",
        "metadata": _k8s_metadata(state, ingress["name"], namespace=state.namespace),
        "spec": {
            "ingressClassName": ingress["class"],
            "rules": [{
                "host": ingress["hosts"],
                "http": {
                    "paths": [{
                        "path": "/",
                        "pathType": "Prefix",
                        "backend": {
                            "service": {
                                "name": ingress["name"],
                                "port": {"number": 8080},
                            },
                        },
                    }],
                },
            }],
        },
        "status": {"loadBalancer": {"ingress": [{"ip": ingress["address"]}]}},
    }


def _k8s_endpointslice(state: SimulationState, endpointslice: dict[str, Any]) -> dict[str, Any]:
    pods = [
        pod for pod in resource_snapshot(state)["pods"]
        if pod["component"] == endpointslice["service"]
    ]
    return {
        "apiVersion": "discovery.k8s.io/v1",
        "kind": "EndpointSlice",
        "metadata": _k8s_metadata(
            state,
            endpointslice["name"],
            namespace=state.namespace,
            labels={"kubernetes.io/service-name": endpointslice["service"]},
        ),
        "addressType": endpointslice["address_type"],
        "ports": [{"name": "http", "protocol": "TCP", "port": 8080}],
        "endpoints": [
            {
                "addresses": [pod["pod_ip"]],
                "conditions": {"ready": pod["status"] == "Running"},
                "targetRef": {"kind": "Pod", "namespace": state.namespace, "name": pod["name"]},
            }
            for pod in pods
        ],
    }


def _k8s_node(state: SimulationState, node: dict[str, Any]) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Node",
        "metadata": _k8s_metadata(state, node["name"], labels={"kubernetes.io/role": node["roles"]}),
        "status": {
            "capacity": {"cpu": "4", "memory": "16384Mi", "pods": "110"},
            "allocatable": {"cpu": "3900m", "memory": "15000Mi", "pods": "110"},
            "conditions": [{
                "type": "Ready",
                "status": "True" if node["status"] == "Ready" else "False",
                "reason": node["status"],
            }],
            "nodeInfo": {"kubeletVersion": node["version"], "osImage": "AMC Linux"},
        },
    }


def _k8s_pod_metrics(state: SimulationState, pod: dict[str, Any]) -> dict[str, Any]:
    return {
        "apiVersion": "metrics.k8s.io/v1beta1",
        "kind": "PodMetrics",
        "metadata": _k8s_metadata(state, pod["name"], namespace=state.namespace),
        "timestamp": _k8s_timestamp(state.clock.now()),
        "window": "30s",
        "containers": [{
            "name": pod["component"],
            "usage": {"cpu": f"{pod['cpu_m']}m", "memory": f"{pod['memory_mi']}Mi"},
        }],
    }


def _k8s_node_metrics(state: SimulationState, node: dict[str, Any]) -> dict[str, Any]:
    return {
        "apiVersion": "metrics.k8s.io/v1beta1",
        "kind": "NodeMetrics",
        "metadata": _k8s_metadata(state, node["name"]),
        "timestamp": _k8s_timestamp(state.clock.now()),
        "window": "30s",
        "usage": {"cpu": f"{node['cpu_m']}m", "memory": f"{node['memory_mi']}Mi"},
    }


def _helm_secret_objects(state: SimulationState) -> list[dict[str, Any]]:
    return [
        _helm_secret_object(state, revision)
        for revision in _helm_release_revisions(state)
    ]


def _helm_release_revisions(state: SimulationState) -> list[dict[str, Any]]:
    base = [
        {"version": 1, "status": "superseded", "description": "Install complete"},
        {"version": 2, "status": "deployed", "description": "Baseline config"},
    ]
    if "deploy_bad_canary_rollback" in state.active_scenarios:
        base = [
            {"version": 1, "status": "superseded", "description": "Install complete"},
            {"version": 2, "status": "superseded", "description": "Baseline config"},
            {"version": 3, "status": "failed", "description": "Canary readiness failed"},
            {"version": 4, "status": "deployed", "description": "Rollback to revision 2"},
        ]
    elif state.profiles():
        base = [
            {"version": 1, "status": "superseded", "description": "Install complete"},
            {"version": 2, "status": "superseded", "description": "Baseline config"},
            {"version": 3, "status": "deployed", "description": _helm_current_description(state)},
        ]
    return state.mutations.current_revisions(base)


def _helm_secret_object(state: SimulationState, revision: dict[str, Any]) -> dict[str, Any]:
    version = int(revision["version"])
    status = str(revision["status"])
    name = f"sh.helm.release.v1.{DEFAULT_RELEASE}.v{version}"
    labels = {
        "owner": "helm",
        "name": DEFAULT_RELEASE,
        "status": status,
        "version": str(version),
    }
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": _k8s_metadata(
            state,
            name,
            namespace=state.namespace,
            labels=labels,
            annotations={
                "meta.helm.sh/release-name": DEFAULT_RELEASE,
                "meta.helm.sh/release-namespace": state.namespace,
            },
        ),
        "type": "helm.sh/release.v1",
        "data": {
            "release": _helm_encoded_release_data(state, revision),
        },
    }


def _helm_encoded_release_data(state: SimulationState, revision: dict[str, Any]) -> str:
    release = _helm_release_payload(state, revision)
    compressed = gzip.compress(json.dumps(release, sort_keys=True).encode("utf-8"))
    helm_encoded = base64.b64encode(compressed)
    return base64.b64encode(helm_encoded).decode("ascii")


def _helm_release_payload(state: SimulationState, revision: dict[str, Any]) -> dict[str, Any]:
    chart_version = DEFAULT_CHART.removeprefix(DEFAULT_RELEASE + "-")
    status = str(revision["status"])
    return {
        "name": DEFAULT_RELEASE,
        "info": {
            "first_deployed": "2026-03-01T00:00:00Z",
            "last_deployed": _k8s_timestamp(state.clock.now()),
            "deleted": "0001-01-01T00:00:00Z",
            "description": revision["description"],
            "status": status,
            "notes": _helm_notes(state),
        },
        "chart": {
            "metadata": {
                "name": DEFAULT_RELEASE,
                "version": chart_version,
                "appVersion": "0.3.0",
                "apiVersion": "v2",
                "description": "Simulated SaaS incident workload",
                "type": "application",
            },
            "templates": [],
            "values": {},
            "files": [],
            "schema": None,
        },
        "config": {
            "replicaCount": 3,
            "namespace": state.namespace,
            "observability": {"otel": True},
            "scenarios": list(state.active_scenarios),
        },
        "manifest": _render_helm_get(state, "manifest"),
        "hooks": [],
        "version": int(revision["version"]),
        "namespace": state.namespace,
        "labels": {},
    }


def _k8s_metadata(
    state: SimulationState,
    name: str,
    *,
    namespace: str = "",
    labels: dict[str, str] | None = None,
    annotations: dict[str, str] | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "name": name,
        "uid": "amc-" + (namespace + "-" if namespace else "") + name,
        "resourceVersion": "1",
        "creationTimestamp": _k8s_timestamp(state.clock.start_time),
        "labels": labels or {},
    }
    if namespace:
        metadata["namespace"] = namespace
    if annotations:
        metadata["annotations"] = annotations
    return metadata


def _k8s_workload_labels(component: str) -> dict[str, str]:
    return {
        "app.kubernetes.io/name": component,
        "app.kubernetes.io/instance": DEFAULT_RELEASE,
        "app.kubernetes.io/managed-by": "Helm",
    }


def _k8s_container_state(state: SimulationState, status_text: str) -> dict[str, Any]:
    if status_text == "Running":
        return {"running": {"startedAt": _k8s_timestamp(state.clock.start_time)}}
    if status_text == "CrashLoopBackOff":
        return {
            "waiting": {
                "reason": "CrashLoopBackOff",
                "message": "back-off restarting failed container",
            },
        }
    if status_text == "Error":
        return {
            "terminated": {
                "reason": "Error",
                "exitCode": 1,
                "startedAt": _k8s_timestamp(state.clock.start_time),
                "finishedAt": _k8s_timestamp(state.clock.now()),
            },
        }
    return {"waiting": {"reason": status_text}}


def _filter_k8s_objects(
    objects: list[dict[str, Any]],
    query: dict[str, list[str]],
) -> list[dict[str, Any]]:
    label_selector = _query_str(query, "labelSelector", "")
    field_selector = _query_str(query, "fieldSelector", "")
    return [
        obj for obj in objects
        if _matches_label_selector(obj.get("metadata", {}).get("labels", {}), label_selector)
        and _matches_field_selector(obj, field_selector)
    ]


def _matches_label_selector(labels: dict[str, str], selector: str) -> bool:
    if not selector:
        return True
    for item in _split_selector(selector):
        if " notin " in item or " notin(" in item:
            key, values = _selector_set_requirement(item, "notin")
            if labels.get(key) in values:
                return False
        elif " in " in item or " in(" in item:
            key, values = _selector_set_requirement(item, "in")
            if labels.get(key) not in values:
                return False
        elif "!=" in item:
            key, value = item.split("!=", 1)
            if labels.get(key.strip()) == value.strip():
                return False
        elif "==" in item or "=" in item:
            separator = "==" if "==" in item else "="
            key, value = item.split(separator, 1)
            if labels.get(key.strip()) != value.strip():
                return False
        elif item.startswith("!"):
            if item[1:].strip() in labels:
                return False
        elif item.strip() not in labels:
            return False
    return True


def _matches_field_selector(obj: dict[str, Any], selector: str) -> bool:
    if not selector:
        return True
    for item in _split_selector(selector):
        if "!=" in item:
            key, value = item.split("!=", 1)
            if str(_nested_field(obj, key.strip())) == value.strip():
                return False
        elif "==" in item or "=" in item:
            separator = "==" if "==" in item else "="
            key, value = item.split(separator, 1)
            if str(_nested_field(obj, key.strip())) != value.strip():
                return False
    return True


def _selector_set_requirement(item: str, operator: str) -> tuple[str, set[str]]:
    key, _, rest = item.partition(operator)
    values = rest.strip()
    if values.startswith("(") and values.endswith(")"):
        values = values[1:-1]
    return key.strip(), {value.strip() for value in values.split(",") if value.strip()}


def _split_selector(selector: str) -> list[str]:
    items = []
    start = 0
    depth = 0
    for index, char in enumerate(selector):
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            items.append(selector[start:index].strip())
            start = index + 1
    items.append(selector[start:].strip())
    return [item for item in items if item]


def _nested_field(obj: dict[str, Any], path: str) -> Any:
    value: Any = obj
    for part in path.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        else:
            return None
    return value


def _k8s_timestamp(value: _dt.datetime) -> str:
    if value.tzinfo is not None:
        value = value.astimezone(_dt.timezone.utc).replace(tzinfo=None)
    return value.replace(microsecond=0).isoformat() + "Z"


def _stable_pod_ip(name: str) -> str:
    value = sum(ord(ch) for ch in name)
    return f"10.244.{value % 200}.{(value // 5) % 240 + 10}"


def _api_trace_body(response: KubernetesApiResponse) -> str:
    if isinstance(response.body, str):
        return _preview(response.body, 2000)
    safe_body = _redact_large_secret_data(response.body)
    return _preview(json.dumps(safe_body, sort_keys=True), 2000)


def _redact_large_secret_data(value: Any) -> Any:
    if isinstance(value, list):
        return [_redact_large_secret_data(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key == "data" and isinstance(item, dict) and "release" in item:
            result[key] = {**item, "release": "<helm release payload>"}
        else:
            result[key] = _redact_large_secret_data(item)
    return result


def _api_namespace(path: str) -> str:
    parts = [part for part in path.strip("/").split("/") if part]
    for index, part in enumerate(parts):
        if part == "namespaces" and index + 1 < len(parts):
            return parts[index + 1]
    return ""


def _api_resource_kind(path: str) -> str:
    parts = [part for part in path.strip("/").split("/") if part]
    for index, part in enumerate(parts):
        if part == "namespaces" and index + 2 < len(parts):
            return parts[index + 2]
    if len(parts) >= 3 and parts[:2] == ["api", "v1"]:
        return parts[2]
    if len(parts) >= 4 and parts[0] == "apis":
        return parts[3]
    return parts[-1] if parts else ""


def _api_resource_name(path: str) -> str:
    parts = [part for part in path.strip("/").split("/") if part]
    for index, part in enumerate(parts):
        if part == "namespaces" and index + 3 < len(parts):
            return parts[index + 3]
    if len(parts) >= 4 and parts[:2] == ["api", "v1"]:
        return parts[3]
    if len(parts) >= 5 and parts[0] == "apis":
        return parts[4]
    return ""


def _api_fingerprint(method: str, path: str) -> str:
    parts = [part for part in path.strip("/").split("/") if part]
    normalized = []
    index = 0
    while index < len(parts):
        part = parts[index]
        normalized.append(part)
        if part == "namespaces" and index + 1 < len(parts):
            normalized.append("{namespace}")
            index += 2
            continue
        if normalized[-1] in {
            "pods",
            "configmaps",
            "secrets",
            "replicationcontrollers",
            "services",
            "endpoints",
            "endpointslices",
            "events",
            "persistentvolumeclaims",
            "serviceaccounts",
            "deployments",
            "replicasets",
            "daemonsets",
            "statefulsets",
            "horizontalpodautoscalers",
            "ingresses",
            "nodes",
            "jobs",
            "cronjobs",
        } and index + 1 < len(parts):
            normalized.append("{name}")
            index += 2
            continue
        index += 1
    return f"kubernetes-api {method} /{'/'.join(normalized)}"


def _api_guess_intent(path: str, response: KubernetesApiResponse) -> str:
    if response.support_status == "supported":
        return "Real kubectl/helm-compatible API call handled by simulator."
    return f"Add Kubernetes API compatibility for {path}."


def make_handler(
    state: SimulationState,
    security: ServerSecurityConfig | None = None,
):
    security = security or ServerSecurityConfig()

    class _Handler(BaseHTTPRequestHandler):
        server_version = "AMCServer/0.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            query = urllib.parse.parse_qs(parsed.query)
            try:
                if path == "/healthz":
                    self._send_text(200, "ok\n")
                    return
                if path == "/readyz":
                    self._send_json(200, {"ready": True})
                    return
                if path == "/" or path == "/debug":
                    self._send_html(200, DEBUG_HTML)
                    return
                if not self._is_authorized():
                    self._send_unauthorized(path)
                    return
                api_started = time.perf_counter()
                api_response = kubernetes_api_response(
                    state,
                    "GET",
                    path,
                    query,
                    self.headers.get("accept", ""),
                )
                if api_response is not None:
                    record_kubernetes_api_call(
                        state,
                        method="GET",
                        path=path,
                        query=query,
                        response=api_response,
                        client=self.client_address[0],
                        user_agent=self.headers.get("user-agent", ""),
                        latency_ms=(time.perf_counter() - api_started) * 1000.0,
                    )
                    self._send_kubernetes_api_response(api_response)
                    return
                if path == "/v1/kubeconfig":
                    self._send_text(
                        200,
                        render_kubeconfig(
                            self._server_url(),
                            state.namespace,
                            token=security.auth_token,
                        ),
                    )
                elif path in {"/v1/state", "/v1/debug/state"}:
                    self._send_json(200, state.summary())
                elif path == "/v1/scenarios":
                    self._send_json(200, _scenario_payload(state))
                elif path == "/v1/anomalies":
                    limit = _query_int(query, "limit", 100)
                    self._send_json(200, {"items": state.generated_rows_slice(limit)})
                elif path == "/v1/debug/resources":
                    self._send_json(200, resource_snapshot(state))
                elif path == "/v1/debug/commands":
                    limit = _query_int(query, "limit", 100)
                    self._send_json(200, {"items": state.traces.list(limit=limit)})
                elif path.startswith("/v1/debug/commands/"):
                    raw_trace_id = path.rsplit("/", 1)[1]
                    try:
                        trace_id = int(raw_trace_id)
                    except ValueError:
                        self._send_json(400, {"error": "trace id must be an integer"})
                        return
                    item = state.traces.get(trace_id)
                    if item is None:
                        self._send_json(404, {"error": "not found"})
                        return
                    self._send_json(200, item)
                elif path == "/v1/debug/unsupported":
                    self._send_json(200, {"items": state.traces.unsupported_summary()})
                elif path == "/v1/debug/search":
                    self._send_json(200, state.traces.search(
                        query=_query_str(query, "q", ""),
                        support_status=_query_str(query, "status", ""),
                        command_family=_query_str(query, "family", ""),
                        scenario_id=_query_str(query, "scenario", ""),
                        limit=_query_int(query, "limit", 50),
                        offset=_query_int(query, "offset", 0),
                    ))
                elif path == "/v1/debug/events":
                    self._send_debug_events()
                elif path == "/v1/logs/stream":
                    self._send_log_stream()
                else:
                    self._send_json(404, {"error": "not found"})
            except Exception as exc:  # pragma: no cover - defensive HTTP boundary
                self._send_json(500, {"error": str(exc)})

        def do_POST(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            path = parsed.path
            try:
                if not self._is_authorized():
                    self._send_unauthorized(path)
                    return
                if path == "/version" or path.startswith(("/api", "/apis")):
                    api_started = time.perf_counter()
                    try:
                        payload = _read_optional_json_body(self, security.max_body_bytes)
                    except RequestBodyTooLarge as exc:
                        api_response = _k8s_status_response(
                            413,
                            str(exc),
                            "RequestEntityTooLarge",
                            "unsupported",
                            "k8s.body.too_large",
                        )
                    else:
                        api_response = kubernetes_api_post_response(state, path, payload)
                    record_kubernetes_api_call(
                        state,
                        method="POST",
                        path=path,
                        query=query,
                        response=api_response,
                        client=self.client_address[0],
                        user_agent=self.headers.get("user-agent", ""),
                        latency_ms=(time.perf_counter() - api_started) * 1000.0,
                    )
                    self._send_kubernetes_api_response(api_response)
                    return
                payload = _read_json_body(self, security.max_body_bytes)
                if path == "/v1/commands":
                    result = run_command(
                        state,
                        command=payload.get("command"),
                        argv=payload.get("argv"),
                        client=self.client_address[0],
                    )
                    self._send_json(200, result)
                elif path == "/v1/time/pause":
                    self._send_json(200, {"clock": {"simulated_time": _format_dt(state.clock.pause()), "paused": True}})
                elif path == "/v1/time/resume":
                    self._send_json(200, {"clock": {"simulated_time": _format_dt(state.clock.resume()), "paused": False}})
                elif path == "/v1/time/seek":
                    timestamp = str(payload.get("timestamp", ""))
                    self._send_json(200, {"clock": {"simulated_time": _format_dt(state.clock.seek(timestamp))}})
                elif path == "/v1/mutations/reset":
                    state.mutations.reset()
                    self._send_json(200, {"mutations": state.mutations.summary()})
                else:
                    self._send_json(404, {"error": "not found"})
            except RequestBodyTooLarge as exc:
                self._send_json(413, {"error": str(exc)})
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)})
            except Exception as exc:
                self._send_json(500, {"error": str(exc)})

        def do_PUT(self) -> None:
            self._handle_mutating_method("PUT")

        def do_PATCH(self) -> None:
            self._handle_mutating_method("PATCH")

        def do_DELETE(self) -> None:
            self._handle_mutating_method("DELETE")

        def _handle_mutating_method(self, method: str) -> None:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            query = urllib.parse.parse_qs(parsed.query)
            if not self._is_authorized():
                self._send_unauthorized(path)
                return
            if path == "/version" or path.startswith(("/api", "/apis")):
                api_started = time.perf_counter()
                try:
                    payload = (
                        _read_json_body(self, security.max_body_bytes)
                        if method in {"PUT", "PATCH"} else {}
                    )
                except RequestBodyTooLarge as exc:
                    api_response = _k8s_status_response(
                        413,
                        str(exc),
                        "RequestEntityTooLarge",
                        "unsupported",
                        "k8s.body.too_large",
                    )
                except ValueError as exc:
                    api_response = _k8s_status_response(
                        400,
                        str(exc),
                        "BadRequest",
                        "unsupported",
                        "k8s.body.invalid",
                    )
                else:
                    api_response = kubernetes_api_mutating_response(state, method, path, payload)
                record_kubernetes_api_call(
                    state,
                    method=method,
                    path=path,
                    query=query,
                    response=api_response,
                    client=self.client_address[0],
                    user_agent=self.headers.get("user-agent", ""),
                    latency_ms=(time.perf_counter() - api_started) * 1000.0,
                )
                self._send_kubernetes_api_response(api_response)
                return
            self._send_json(405, {"error": f"{method} is not supported"})

        def _is_authorized(self) -> bool:
            if not security.auth_token:
                return True
            supplied = self.headers.get("authorization", "")
            expected = f"Bearer {security.auth_token}"
            return hmac.compare_digest(supplied, expected)

        def _send_unauthorized(self, path: str) -> None:
            if path == "/version" or path.startswith(("/api", "/apis")):
                response = _k8s_status_response(
                    401,
                    "missing or invalid bearer token",
                    "Unauthorized",
                    "unsupported",
                    "k8s.auth.unauthorized",
                )
                self._send_kubernetes_api_response(response)
                return
            self._send_json(
                401,
                {"error": "missing or invalid bearer token"},
                extra_headers={"www-authenticate": "Bearer"},
            )

        def _send_json(
            self,
            status: int,
            payload: Any,
            *,
            extra_headers: dict[str, str] | None = None,
        ) -> None:
            body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
            self.send_response(status)
            self._send_common_headers(
                "application/json; charset=utf-8",
                len(body),
                extra_headers=extra_headers,
            )
            self.end_headers()
            self.wfile.write(body)

        def _send_text(self, status: int, text: str) -> None:
            body = text.encode("utf-8")
            self.send_response(status)
            self._send_common_headers("text/plain; charset=utf-8", len(body))
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, status: int, text: str) -> None:
            body = text.encode("utf-8")
            self.send_response(status)
            self._send_common_headers("text/html; charset=utf-8", len(body))
            self.end_headers()
            self.wfile.write(body)

        def _send_kubernetes_api_response(self, response: KubernetesApiResponse) -> None:
            if isinstance(response.body, str):
                body = response.body.encode("utf-8")
            else:
                body = json.dumps(response.body, indent=2, sort_keys=True).encode("utf-8")
            self.send_response(response.status)
            extra_headers = None
            if response.status == 401:
                extra_headers = {"www-authenticate": "Bearer"}
            self._send_common_headers(
                response.content_type,
                len(body),
                extra_headers=extra_headers,
            )
            self.end_headers()
            self.wfile.write(body)

        def _send_common_headers(
            self,
            content_type: str,
            content_length: int,
            *,
            extra_headers: dict[str, str] | None = None,
        ) -> None:
            self.send_header("content-type", content_type)
            self.send_header("content-length", str(content_length))
            self.send_header("cache-control", "no-store")
            self.send_header("x-content-type-options", "nosniff")
            self.send_header("referrer-policy", "no-referrer")
            self.send_header("x-frame-options", "DENY")
            for key, value in (extra_headers or {}).items():
                self.send_header(key, value)

        def _server_url(self) -> str:
            host = self.headers.get("host")
            if not host:
                bound_host, bound_port = self.server.server_address[:2]
                host = f"{bound_host}:{bound_port}"
            return f"http://{host}"

        def _send_debug_events(self) -> None:
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("cache-control", "no-cache")
            self.send_header("x-content-type-options", "nosniff")
            self.send_header("referrer-policy", "no-referrer")
            self.end_headers()
            last_version = -1
            for _ in range(300):
                version = state.traces.version
                if version != last_version:
                    payload = json.dumps({"version": version})
                    if not self._write_event_stream(
                        f"event: commands\ndata: {payload}\n\n".encode("utf-8")
                    ):
                        return
                    if not self._flush_event_stream():
                        return
                    last_version = version
                time.sleep(1.0)

        def _write_event_stream(self, payload: bytes) -> bool:
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                return False
            return True

        def _flush_event_stream(self) -> bool:
            try:
                self.wfile.flush()
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                return False
            return True

        def _send_log_stream(self) -> None:
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("cache-control", "no-cache")
            self.send_header("x-content-type-options", "nosniff")
            self.send_header("referrer-policy", "no-referrer")
            self.end_headers()
            last_generation = -1
            last_signature: tuple[int, int] | None = None
            for _ in range(300):
                with state.generation.lock:
                    generation = state.generation.generation_count
                    last_seed = state.generation.last_seed
                signature = _log_file_signature(state.output_dir / "metric_report.log")
                if generation != last_generation or signature != last_signature:
                    payload = json.dumps({
                        "generation_count": generation,
                        "last_seed": last_seed,
                    })
                    if not self._write_event_stream(
                        f"event: generation\ndata: {payload}\n\n".encode("utf-8")
                    ):
                        return
                    if not self._send_log_file():
                        return
                    if not self._flush_event_stream():
                        return
                    last_generation = generation
                    last_signature = signature
                time.sleep(1.0)

        def _send_log_file(self) -> bool:
            log_path = state.output_dir / "metric_report.log"
            if not log_path.exists():
                payload = json.dumps({"line": "metric_report.log is not present for this run"})
                return self._write_event_stream(f"data: {payload}\n\n".encode("utf-8"))
            with open(log_path, encoding="utf-8") as f:
                for line in f:
                    payload = json.dumps({"line": line.rstrip("\n")})
                    if not self._write_event_stream(f"data: {payload}\n\n".encode("utf-8")):
                        return False
            return True

    return _Handler


def _log_file_signature(path: Path) -> tuple[int, int] | None:
    with contextlib.suppress(OSError):
        stat = path.stat()
        return stat.st_mtime_ns, stat.st_size
    return None


def _query_int(query: dict[str, list[str]], name: str, default: int) -> int:
    try:
        return int(query.get(name, [str(default)])[0])
    except ValueError:
        return default


def _query_str(query: dict[str, list[str]], name: str, default: str) -> str:
    return query.get(name, [default])[0].strip()


def _scenario_payload(state: SimulationState) -> dict[str, Any]:
    return {
        "active": list(state.active_scenarios),
        "known": [
            _scenario_detail_payload(state, slug, scenario)
            for slug, scenario in state.legacy.SCENARIOS.items()
        ],
    }


def _scenario_detail_payload(state: SimulationState, slug: str, scenario: Any) -> dict[str, Any]:
    profile = OPS_SCENARIO_PROFILES.get(slug)
    return {
        "id": slug,
        "name": scenario.name,
        "severity": scenario.severity,
        "days_required": scenario.days_required,
        "category": scenario.category,
        "active": slug in state.active_scenarios,
        "components_touched": list(scenario.components_touched),
        "primary_specs": [
            _scenario_spec_payload(component, spec)
            for component, spec in scenario.primary_specs
        ],
        "cascade_specs": [
            _scenario_spec_payload(component, spec)
            for component, spec in scenario.cascade_specs
        ],
        "ops_profile": profile is not None,
        "ops_profile_detail": _ops_profile_payload(profile) if profile is not None else None,
    }


def _scenario_spec_payload(component: str, spec: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "component": component,
        "metric": str(spec.get("metric", "")),
        "description": str(spec.get("description", "")),
        "time_offset_seconds": spec.get("time_offset"),
        "duration_seconds": spec.get("duration_seconds"),
        "shape": spec.get("shape"),
        "severity": spec.get("severity", ""),
    }
    shape_params = spec.get("shape_params")
    if shape_params is not None:
        payload["shape_params"] = _json_safe_payload(shape_params)
    instance_filter = spec.get("instance_filter")
    if instance_filter is not None:
        payload["instance_filter"] = _json_safe_payload(instance_filter)
    return payload


def _ops_profile_payload(profile: OpsScenarioProfile) -> dict[str, Any]:
    return {
        "summary": profile.summary,
        "affected_components": list(profile.affected_components),
        "events": list(profile.events),
        "logs": list(profile.logs),
        "helm_notes": profile.helm_notes,
        "rollout_note": profile.rollout_note,
        "impacts": [
            {
                "component": impact.component,
                "deployment_status": impact.deployment_status,
                "pod_status": impact.pod_status,
                "ready": impact.ready,
                "ready_replicas": impact.ready_replicas,
                "ready_replicas_delta": impact.ready_replicas_delta,
                "restarts": impact.restarts,
                "cpu_pct": impact.cpu_pct,
                "cpu_m": impact.cpu_m,
                "memory_mi": impact.memory_mi,
                "memory_pct": impact.memory_pct,
                "pvc_used_pct": impact.pvc_used_pct,
            }
            for impact in profile.impacts
        ],
    }


def _json_safe_payload(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if callable(value):
        return "<callable>"
    if isinstance(value, dict):
        return {
            str(key): _json_safe_payload(value[key])
            for key in sorted(value, key=str)
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe_payload(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(
            (_json_safe_payload(item) for item in value),
            key=_json_safe_sort_key,
        )
    return str(value)


def _json_safe_sort_key(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


DEBUG_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AMC Debug Console</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f5f7f8;
      --panel: #ffffff;
      --line: #d8dee4;
      --text: #1f2933;
      --muted: #5f6b76;
      --ok: #0f7b43;
      --warn: #a15c00;
      --bad: #b42318;
      --accent: #1b65a7;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--text);
      background: var(--bg);
    }
    header {
      position: sticky;
      top: 0;
      z-index: 3;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 12px 18px;
      border-bottom: 1px solid var(--line);
      background: #ffffff;
    }
    h1, h2 { margin: 0; font-weight: 650; letter-spacing: 0; }
    h1 { font-size: 18px; }
    h2 { font-size: 15px; }
    main {
      display: grid;
      grid-template-columns: minmax(0, 1.4fr) minmax(340px, 0.8fr);
      gap: 12px;
      padding: 12px;
    }
    section {
      min-width: 0;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--panel);
    }
    section > .bar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      padding: 10px 12px;
      border-bottom: 1px solid var(--line);
    }
    .stack { display: grid; gap: 12px; }
    .metrics {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 8px;
      padding: 12px;
    }
    .metric {
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px;
      min-height: 62px;
    }
    .label { color: var(--muted); font-size: 12px; }
    .value { margin-top: 3px; font-size: 18px; font-weight: 650; overflow-wrap: anywhere; }
    table {
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
    }
    th, td {
      padding: 8px 10px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
      overflow-wrap: anywhere;
    }
    th { color: var(--muted); font-size: 12px; font-weight: 650; }
    tr:hover td { background: #f9fbfc; }
    .status {
      display: inline-flex;
      align-items: center;
      min-height: 22px;
      padding: 1px 7px;
      border-radius: 999px;
      border: 1px solid var(--line);
      font-size: 12px;
      font-weight: 650;
      white-space: nowrap;
    }
    .supported { color: var(--ok); border-color: #9dd7b8; background: #eefaf3; }
    .partial { color: var(--warn); border-color: #e8c68e; background: #fff8eb; }
    .unsupported { color: var(--bad); border-color: #efaaa3; background: #fff1f0; }
    tr.selected td { background: #eef5fb; }
    #commands tr, #searchResults tr, #scenarios tr, #profiles tr { cursor: pointer; }
    .detail-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      padding: 0 12px 12px;
    }
    .detail-grid table, .scenario-detail table { font-size: 12px; }
    .detail-grid th, .scenario-detail th { width: 42%; }
    .scenario-shell { padding: 12px; }
    .scenario-detail {
      border-top: 1px solid var(--line);
      padding-top: 12px;
      margin-top: 12px;
    }
    .scenario-head {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 10px;
    }
    .scenario-head h3 { margin: 0; font-size: 15px; letter-spacing: 0; }
    .scenario-head .muted { font-size: 12px; }
    .scenario-section-title {
      margin: 12px 0 6px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
    }
    .cmdbar {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 8px;
      padding: 12px;
      border-bottom: 1px solid var(--line);
    }
    .searchbar {
      display: grid;
      grid-template-columns: minmax(0, 1.4fr) minmax(0, 0.75fr) minmax(0, 0.75fr) auto;
      gap: 8px;
      padding: 12px;
      border-bottom: 1px solid var(--line);
    }
    input, button, select {
      font: inherit;
      border: 1px solid var(--line);
      border-radius: 6px;
      min-height: 34px;
      padding: 6px 9px;
      background: #fff;
    }
    button {
      color: #fff;
      background: var(--accent);
      border-color: var(--accent);
      cursor: pointer;
      font-weight: 650;
    }
    pre {
      margin: 0;
      padding: 12px;
      max-height: 520px;
      overflow: auto;
      border-top: 1px solid var(--line);
      background: #0f1720;
      color: #e5edf5;
      font-size: 12px;
      line-height: 1.45;
    }
    .muted { color: var(--muted); }
    @media (max-width: 1000px) {
      main { grid-template-columns: 1fr; }
      .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
  </style>
</head>
<body>
  <header>
    <h1>AMC Debug Console</h1>
    <div class="muted" id="clock">--</div>
  </header>
  <main>
    <div class="stack">
      <section>
        <div class="bar">
          <h2>State</h2>
          <span id="otel" class="muted"></span>
          <button type="button" id="resetMutations">Reset</button>
        </div>
        <div class="metrics">
          <div class="metric"><div class="label">Scenarios</div><div class="value" id="scenarioCount">0</div></div>
          <div class="metric"><div class="label">Anomalies</div><div class="value" id="anomalyCount">0</div></div>
          <div class="metric"><div class="label">Commands</div><div class="value" id="commandCount">0</div></div>
          <div class="metric"><div class="label">Unsupported</div><div class="value" id="unsupportedCount">0</div></div>
          <div class="metric"><div class="label">Generations</div><div class="value" id="generationCount">0</div></div>
          <div class="metric"><div class="label">Mutations</div><div class="value" id="mutationVersion">0</div></div>
        </div>
        <div class="detail-grid">
          <table>
            <thead><tr><th colspan="2">Runtime</th></tr></thead>
            <tbody id="runtimeDetails"></tbody>
          </table>
          <table>
            <thead><tr><th colspan="2">Mutable State</th></tr></thead>
            <tbody id="mutationDetails"></tbody>
          </table>
        </div>
        <table>
          <thead><tr><th>Workload Overlay</th><th style="width:95px">Replicas</th><th style="width:130px">Status</th><th style="width:170px">Updated</th></tr></thead>
          <tbody id="workloadMutations"></tbody>
        </table>
      </section>
      <section>
        <div class="bar"><h2>Command Trace</h2><span class="muted" id="lastRefresh"></span></div>
        <form class="cmdbar" id="commandForm">
          <input id="commandInput" value="kubectl get pods -n saas-prod">
          <button type="submit">Run</button>
        </form>
        <table>
          <thead><tr><th style="width:70px">ID</th><th>Command</th><th style="width:125px">Status</th><th style="width:110px">Exit</th></tr></thead>
          <tbody id="commands"></tbody>
        </table>
        <pre id="details">{}</pre>
      </section>
      <section>
        <div class="bar"><h2>Resources</h2><span class="muted">pods</span></div>
        <table>
          <thead><tr><th>Name</th><th>Status</th><th>Restarts</th><th>Scenario</th></tr></thead>
          <tbody id="pods"></tbody>
        </table>
      </section>
    </div>
    <div class="stack">
      <section>
        <div class="bar"><h2>Search</h2><span class="muted" id="searchCount">0 matches</span></div>
        <form class="searchbar" id="searchForm">
          <input id="searchInput" placeholder="raw command, output, fingerprint">
          <select id="searchStatus">
            <option value="">any status</option>
            <option value="supported">supported</option>
            <option value="partial">partial</option>
            <option value="unsupported">unsupported</option>
          </select>
          <select id="searchFamily">
            <option value="">any family</option>
            <option value="kubectl">kubectl</option>
            <option value="helm">helm</option>
            <option value="kubernetes-api">kubernetes-api</option>
          </select>
          <button type="submit">Search</button>
        </form>
        <table>
          <thead><tr><th>Command</th><th style="width:112px">Status</th></tr></thead>
          <tbody id="searchResults"></tbody>
        </table>
      </section>
      <section>
        <div class="bar"><h2>Unsupported Explorer</h2><span class="muted">grouped</span></div>
        <table>
          <thead><tr><th>Fingerprint</th><th style="width:70px">Count</th></tr></thead>
          <tbody id="unsupported"></tbody>
        </table>
      </section>
      <section>
        <div class="bar"><h2>Active Profiles</h2><span class="muted">scenario overlays</span></div>
        <table>
          <thead><tr><th>Scenario</th><th>Summary</th></tr></thead>
          <tbody id="profiles"></tbody>
        </table>
      </section>
      <section>
        <div class="bar"><h2>Scenario Catalog</h2><span class="muted" id="scenarioCatalogCount">0 scenarios</span></div>
        <div class="scenario-shell">
          <table>
            <thead><tr><th>Scenario</th><th style="width:88px">Severity</th><th style="width:110px">Days</th></tr></thead>
            <tbody id="scenarios"></tbody>
          </table>
          <div class="scenario-detail" id="scenarioDetail"></div>
        </div>
      </section>
      <section>
        <div class="bar"><h2>Recent Events</h2><span class="muted">cluster</span></div>
        <table>
          <thead><tr><th>Reason</th><th>Object</th><th>Message</th></tr></thead>
          <tbody id="events"></tbody>
        </table>
      </section>
    </div>
  </main>
  <script>
    const $ = (id) => document.getElementById(id);
    const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[ch]));
    const AUTH_STORAGE_KEY = "amc.debug.authToken";
    let authPromptDismissed = false;
    let scenarioCatalog = {active: [], known: []};
    let scenarioCatalogPromise = null;
    let scenarioCatalogRendered = false;
    let selectedScenarioId = "";
    function bootstrapAuthToken() {
      const params = new URLSearchParams(window.location.search);
      const token = params.get("token") || params.get("auth_token");
      if (!token) return;
      window.localStorage.setItem(AUTH_STORAGE_KEY, token);
      params.delete("token");
      params.delete("auth_token");
      const query = params.toString();
      window.history.replaceState(
        null,
        "",
        `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`
      );
    }
    async function request(url, options = {}, retryAuth = true) {
      const token = window.localStorage.getItem(AUTH_STORAGE_KEY);
      const res = await fetch(url, {
        ...options,
        headers: token
          ? {...(options.headers || {}), authorization: `Bearer ${token}`}
          : (options.headers || {})
      });
      if (res.status === 401 && retryAuth) {
        const currentToken = window.localStorage.getItem(AUTH_STORAGE_KEY);
        if (currentToken && currentToken !== token) return await request(url, options, false);
        if (authPromptDismissed) throw new Error(`${res.status} ${res.statusText}`);
        const token = window.prompt("Bearer token");
        if (token) {
          window.localStorage.setItem(AUTH_STORAGE_KEY, token);
          return await request(url, options, false);
        }
        authPromptDismissed = true;
      }
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      return res;
    }
    async function getJSON(url) {
      const res = await request(url);
      return await res.json();
    }
    async function getScenarioCatalog() {
      if (!scenarioCatalogPromise) {
        scenarioCatalogPromise = getJSON("/v1/scenarios")
          .then((payload) => {
            scenarioCatalog = payload;
            return payload;
          })
          .catch((error) => {
            scenarioCatalogPromise = null;
            throw error;
          });
      }
      return await scenarioCatalogPromise;
    }
    async function postJSON(url, body) {
      const res = await request(url, {
        method: "POST",
        headers: {"content-type": "application/json"},
        body: JSON.stringify(body)
      });
      return await res.json();
    }
    function statusClass(status) {
      return status === "supported" ? "supported" : status === "partial" ? "partial" : "unsupported";
    }
    function severityClass(severity) {
      return severity === "high" ? "unsupported" : severity === "medium" ? "partial" : "supported";
    }
    function valueOrDash(value) {
      return value === undefined || value === null || value === "" ? "-" : value;
    }
    function renderKeyValues(targetId, rows) {
      $(targetId).innerHTML = rows.map(([key, value]) => `
        <tr><th>${esc(key)}</th><td>${esc(valueOrDash(value))}</td></tr>
      `).join("");
    }
    function formatSeconds(seconds) {
      if (seconds === undefined || seconds === null || seconds === "") return "-";
      const value = Number(seconds);
      if (!Number.isFinite(value)) return String(seconds);
      if (value >= 3600) return `${Math.round((value / 3600) * 10) / 10}h`;
      if (value >= 60) return `${Math.round((value / 60) * 10) / 10}m`;
      return `${value}s`;
    }
    function formatOffset(seconds) {
      return formatSeconds(seconds);
    }
    function selectScenario(id) {
      selectedScenarioId = id;
      renderScenarioCatalog(scenarioCatalog);
    }
    function renderCommands(items) {
      $("commands").innerHTML = items.map((item) => `
        <tr data-id="${item.id}">
          <td>${item.id}</td>
          <td><code>${esc(item.raw_input)}</code><div class="muted">${esc(item.matched_rule_id)}</div></td>
          <td><span class="status ${statusClass(item.support_status)}">${esc(item.support_status)}</span></td>
          <td>${item.exit_code}</td>
        </tr>`).join("");
      document.querySelectorAll("#commands tr").forEach((row) => {
        row.addEventListener("click", async () => {
          const item = await getJSON(`/v1/debug/commands/${row.dataset.id}`);
          $("details").textContent = JSON.stringify(item, null, 2);
        });
      });
    }
    function renderState(state) {
      $("clock").textContent = `${state.clock.simulated_time} @ ${state.clock.speedup}x`;
      const otelText = state.otel.enabled ? `OTEL ${state.otel.thread}` : "OTEL off";
      const generationText = state.generation.enabled
        ? `generation ${state.generation.thread}`
        : "generation off";
      $("otel").textContent = `${otelText} - ${generationText}`;
      $("scenarioCount").textContent = state.active_scenarios.length;
      $("anomalyCount").textContent = state.anomaly_count;
      $("commandCount").textContent = state.command_trace_count;
      $("unsupportedCount").textContent = state.unsupported_group_count;
      $("generationCount").textContent = state.generation.generation_count;
      $("mutationVersion").textContent = state.mutations.version;
      const release = state.mutations.release || {};
      renderKeyValues("runtimeDetails", [
        ["Generation", `${state.generation.enabled ? "on" : "off"} / ${state.generation.thread}`],
        ["Interval", state.generation.enabled ? `${state.generation.interval_seconds}s` : "-"],
        ["Last seed", state.generation.last_seed],
        ["Last generated", state.generation.last_completed_at],
        ["Generation error", state.generation.last_error],
        ["OTEL", state.otel.enabled ? state.otel.thread : "off"],
        ["OTEL batches", state.otel.stream_batches],
        ["Last OTEL", state.otel.last_completed_at],
      ]);
      renderKeyValues("mutationDetails", [
        ["Version", state.mutations.version],
        ["Workloads", Object.keys(state.mutations.workloads || {}).length],
        ["Deleted pods", (state.mutations.deleted_pods || []).join(", ")],
        ["Created resources", Object.values(state.mutations.created_resources || {}).reduce((total, items) => total + items.length, 0)],
        ["Deleted resources", Object.values(state.mutations.deleted_resources || {}).reduce((total, items) => total + items.length, 0)],
        ["Extra events", state.mutations.extra_event_count],
        ["Release", release.uninstalled ? "uninstalled" : `${release.revision_count || 0} revisions`],
        ["Release updated", release.updated_at],
      ]);
      renderWorkloadMutations(state.mutations.workloads || {});
      $("profiles").innerHTML = state.profiles.map((item) => `
        <tr data-id="${esc(item.scenario_id)}"><td>${esc(item.scenario_id)}</td><td>${esc(item.summary)}</td></tr>
      `).join("");
      document.querySelectorAll("#profiles tr").forEach((row) => {
        row.addEventListener("click", () => selectScenario(row.dataset.id));
      });
    }
    function renderWorkloadMutations(workloads) {
      const entries = Object.entries(workloads).sort(([left], [right]) => left.localeCompare(right));
      $("workloadMutations").innerHTML = entries.length ? entries.map(([name, mutation]) => `
        <tr>
          <td>${esc(name)}</td>
          <td>${esc(valueOrDash(mutation.replicas))}</td>
          <td>${esc(valueOrDash(mutation.deployment_status || mutation.pod_status))}</td>
          <td>${esc(valueOrDash(mutation.updated_at))}</td>
        </tr>
      `).join("") : `<tr><td colspan="4" class="muted">none</td></tr>`;
    }
    function renderResources(resources) {
      $("pods").innerHTML = resources.pods.map((pod) => `
        <tr>
          <td>${esc(pod.name)}</td>
          <td>${esc(pod.status)}</td>
          <td>${pod.restarts}</td>
          <td>${esc((pod.scenario_ids || []).join(", "))}</td>
        </tr>`).join("");
      $("events").innerHTML = resources.events.map((event) => `
        <tr><td>${esc(event.reason)}</td><td>${esc(event.object)}</td><td>${esc(event.message)}</td></tr>
      `).join("");
    }
    function renderUnsupported(payload) {
      $("unsupported").innerHTML = payload.items.map((item) => `
        <tr>
          <td>${esc(item.fingerprint)}<div class="muted">${esc(item.guessed_intent)}</div></td>
          <td>${item.count}</td>
        </tr>`).join("");
    }
    function renderSearch(payload) {
      $("searchCount").textContent = `${payload.total} match${payload.total === 1 ? "" : "es"}`;
      $("searchResults").innerHTML = payload.items.map((item) => `
        <tr data-id="${item.id}">
          <td><code>${esc(item.raw_input)}</code><div class="muted">${esc(item.fingerprint)}</div></td>
          <td><span class="status ${statusClass(item.support_status)}">${esc(item.support_status)}</span></td>
        </tr>`).join("");
      document.querySelectorAll("#searchResults tr").forEach((row) => {
        row.addEventListener("click", async () => {
          const item = await getJSON(`/v1/debug/commands/${row.dataset.id}`);
          $("details").textContent = JSON.stringify(item, null, 2);
        });
      });
    }
    function renderScenarioCatalog(payload) {
      scenarioCatalog = payload;
      const known = payload.known || [];
      const active = new Set(payload.active || []);
      $("scenarioCatalogCount").textContent = `${known.length} scenario${known.length === 1 ? "" : "s"}`;
      if (!selectedScenarioId || !known.some((item) => item.id === selectedScenarioId)) {
        selectedScenarioId = known.find((item) => active.has(item.id))?.id || known[0]?.id || "";
      }
      $("scenarios").innerHTML = known.map((item) => `
        <tr data-id="${esc(item.id)}" class="${item.id === selectedScenarioId ? "selected" : ""}">
          <td>
            <strong>${esc(item.id)}</strong>
            <div class="muted">${esc(item.name)}</div>
            <div class="muted">${esc((item.components_touched || []).join(", "))}</div>
          </td>
          <td>
            <span class="status ${severityClass(item.severity)}">${esc(item.severity)}</span>
            ${active.has(item.id) ? `<div class="muted">active</div>` : ""}
          </td>
          <td>${esc(item.days_required)}</td>
        </tr>
      `).join("");
      document.querySelectorAll("#scenarios tr").forEach((row) => {
        row.addEventListener("click", () => selectScenario(row.dataset.id));
      });
      renderScenarioDetail(known.find((item) => item.id === selectedScenarioId));
    }
    function renderScenarioCatalogOnce(payload) {
      if (scenarioCatalogRendered) return;
      renderScenarioCatalog(payload);
      scenarioCatalogRendered = true;
    }
    function renderSpecRows(specs) {
      if (!specs.length) return `<tr><td colspan="4" class="muted">none</td></tr>`;
      return specs.map((spec) => `
        <tr>
          <td>${esc(spec.component)}<div class="muted">${esc(spec.metric)}</div></td>
          <td>${esc(spec.description)}</td>
          <td>${esc(formatOffset(spec.time_offset_seconds))}</td>
          <td>${esc(formatSeconds(spec.duration_seconds))}</td>
        </tr>
      `).join("");
    }
    function renderStringRows(items) {
      if (!items.length) return `<tr><td class="muted">none</td></tr>`;
      return items.map((item) => `<tr><td>${esc(item)}</td></tr>`).join("");
    }
    function renderImpactRows(impacts) {
      if (!impacts.length) return `<tr><td colspan="4" class="muted">none</td></tr>`;
      return impacts.map((impact) => `
        <tr>
          <td>${esc(impact.component)}</td>
          <td>${esc(impact.deployment_status)}</td>
          <td>${esc(impact.pod_status)}</td>
          <td>${esc(valueOrDash(impact.ready || impact.ready_replicas || impact.ready_replicas_delta))}</td>
        </tr>
      `).join("");
    }
    function renderScenarioDetail(item) {
      if (!item) {
        $("scenarioDetail").innerHTML = `<div class="muted">none</div>`;
        return;
      }
      const profile = item.ops_profile_detail || {};
      $("scenarioDetail").innerHTML = `
        <div class="scenario-head">
          <div>
            <h3>${esc(item.name)}</h3>
            <div class="muted">${esc(item.id)} - ${esc(item.category)} - ${esc((item.components_touched || []).join(", "))}</div>
          </div>
          <span class="status ${severityClass(item.severity)}">${esc(item.severity)}</span>
        </div>
        <div class="detail-grid">
          <table>
            <tbody>
              <tr><th>Days required</th><td>${esc(item.days_required)}</td></tr>
              <tr><th>Primary signals</th><td>${esc((item.primary_specs || []).length)}</td></tr>
              <tr><th>Cascade signals</th><td>${esc((item.cascade_specs || []).length)}</td></tr>
              <tr><th>Ops profile</th><td>${profile.summary ? "yes" : "no"}</td></tr>
            </tbody>
          </table>
          <table>
            <tbody>
              <tr><th>Operator summary</th><td>${esc(valueOrDash(profile.summary))}</td></tr>
              <tr><th>Affected</th><td>${esc((profile.affected_components || []).join(", "))}</td></tr>
              <tr><th>Rollout note</th><td>${esc(valueOrDash(profile.rollout_note))}</td></tr>
              <tr><th>Helm notes</th><td>${esc(valueOrDash(profile.helm_notes))}</td></tr>
            </tbody>
          </table>
        </div>
        <div class="scenario-section-title">Primary Signals</div>
        <table>
          <thead><tr><th>Component / Metric</th><th>Description</th><th style="width:80px">Offset</th><th style="width:90px">Duration</th></tr></thead>
          <tbody>${renderSpecRows(item.primary_specs || [])}</tbody>
        </table>
        <div class="scenario-section-title">Cascade Signals</div>
        <table>
          <thead><tr><th>Component / Metric</th><th>Description</th><th style="width:80px">Offset</th><th style="width:90px">Duration</th></tr></thead>
          <tbody>${renderSpecRows(item.cascade_specs || [])}</tbody>
        </table>
        <div class="scenario-section-title">Ops Events</div>
        <table><tbody>${renderStringRows(profile.events || [])}</tbody></table>
        <div class="scenario-section-title">Ops Logs</div>
        <table><tbody>${renderStringRows(profile.logs || [])}</tbody></table>
        <div class="scenario-section-title">Kubernetes Impacts</div>
        <table>
          <thead><tr><th>Component</th><th>Status</th><th>Pod</th><th>Ready</th></tr></thead>
          <tbody>${renderImpactRows(profile.impacts || [])}</tbody>
        </table>
      `;
    }
    async function runSearch() {
      const params = new URLSearchParams();
      if ($("searchInput").value) params.set("q", $("searchInput").value);
      if ($("searchStatus").value) params.set("status", $("searchStatus").value);
      if ($("searchFamily").value) params.set("family", $("searchFamily").value);
      params.set("limit", "25");
      const payload = await getJSON(`/v1/debug/search?${params.toString()}`);
      renderSearch(payload);
    }
    async function refresh() {
      try {
        const [state, commands, unsupported, resources, scenarios] = await Promise.all([
          getJSON("/v1/state"),
          getJSON("/v1/debug/commands?limit=80"),
          getJSON("/v1/debug/unsupported"),
          getJSON("/v1/debug/resources"),
          getScenarioCatalog()
        ]);
        renderState(state);
        renderCommands(commands.items);
        renderUnsupported(unsupported);
        renderResources(resources);
        renderScenarioCatalogOnce(scenarios);
        await runSearch();
        $("lastRefresh").textContent = new Date().toLocaleTimeString();
      } catch (error) {
        $("details").textContent = String(error);
      }
    }
    $("commandForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      const result = await postJSON("/v1/commands", {command: $("commandInput").value});
      $("details").textContent = JSON.stringify(result, null, 2);
      await refresh();
    });
    $("searchForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      await runSearch();
    });
    $("resetMutations").addEventListener("click", async () => {
      const result = await postJSON("/v1/mutations/reset", {});
      $("details").textContent = JSON.stringify(result, null, 2);
      await refresh();
    });
    bootstrapAuthToken();
    refresh();
    setInterval(refresh, 1500);
  </script>
</body>
</html>
"""


def _build_serve_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="anomaly-metric-creator.py serve",
        description="Run the anomaly simulator as an HTTP server with Kubernetes/Helm command responses.",
        epilog=(
            "Any unrecognized options are parsed as normal generate options "
            "(for example --scenarios, --components, --duration-days, "
            "--otel-send, and --otel-endpoint)."
        ),
        allow_abbrev=False,
    )
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind host (default: 127.0.0.1).")
    parser.add_argument("--port", type=int, default=8088, help="HTTP bind port (default: 8088; use 0 for ephemeral).")
    parser.add_argument("--namespace", default=DEFAULT_NAMESPACE, help=f"Simulated Kubernetes namespace (default: {DEFAULT_NAMESPACE}).")
    parser.add_argument("--debug-ring-size", type=int, default=DEFAULT_TRACE_LIMIT, help=f"Command trace ring size (default: {DEFAULT_TRACE_LIMIT}).")
    parser.add_argument("--persist-command-log", type=Path, default=None, help="Optional JSONL path for command traces.")
    parser.add_argument("--persist-command-db", type=Path, default=None, help="Optional SQLite path for durable command traces and search.")
    parser.add_argument("--auth-token", default="", help="Optional bearer token required for simulator HTTP and Kubernetes API requests.")
    parser.add_argument("--max-request-body-bytes", type=int, default=DEFAULT_MAX_BODY_BYTES, help=f"Maximum accepted HTTP request body size (default: {DEFAULT_MAX_BODY_BYTES}).")
    parser.add_argument("--allow-remote-without-auth", action="store_true", help="Allow non-loopback binds without --auth-token for isolated lab use.")
    parser.add_argument("--no-generate", action="store_true", help="Use existing artifacts in --output-dir instead of generating before serving.")
    parser.add_argument("--continuous-generate", action="store_true", help="Continuously regenerate artifacts while the server runs.")
    parser.add_argument("--continuous-generate-interval-seconds", type=float, default=60.0, help="Seconds between continuous generation passes (default: 60).")
    return parser


def _is_loopback_bind_host(host: str) -> bool:
    value = host.strip().strip("[]").lower()
    if value in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def serve_main(argv: list[str] | None = None, *, legacy_module: Any | None = None) -> None:
    if legacy_module is None:
        from . import legacy as legacy_module

    parser = _build_serve_parser()
    serve_args, generate_argv = parser.parse_known_args(list(argv or []))
    if serve_args.debug_ring_size < 1:
        parser.error("--debug-ring-size must be >= 1")
    if serve_args.port < 0 or serve_args.port > 65535:
        parser.error("--port must be in [0, 65535]")
    if serve_args.max_request_body_bytes < 1:
        parser.error("--max-request-body-bytes must be >= 1")
    if serve_args.continuous_generate_interval_seconds <= 0:
        parser.error("--continuous-generate-interval-seconds must be > 0")
    if (
        not _is_loopback_bind_host(serve_args.host)
        and not serve_args.auth_token
        and not serve_args.allow_remote_without_auth
    ):
        parser.error(
            "--host binds outside loopback; pass --auth-token or "
            "--allow-remote-without-auth"
        )

    args = legacy_module.parse_args(generate_argv)
    if not serve_args.no_generate:
        run_argv = _generation_argv_without_otel(generate_argv)
        legacy_module.main(run_argv)
        args = legacy_module.parse_args(generate_argv)

    state = build_state(
        legacy_module,
        args,
        namespace=serve_args.namespace,
        trace_limit=serve_args.debug_ring_size,
        persist_command_log=serve_args.persist_command_log,
        persist_command_db=serve_args.persist_command_db,
    )
    generation_stop = _start_continuous_generation(
        state,
        generate_argv,
        enabled=serve_args.continuous_generate,
        interval_seconds=serve_args.continuous_generate_interval_seconds,
        stream_otel=bool(getattr(args, "otel_enabled", False)),
    )
    if not serve_args.continuous_generate:
        _start_otel_background(state)

    security = ServerSecurityConfig(
        auth_token=serve_args.auth_token,
        max_body_bytes=serve_args.max_request_body_bytes,
        allow_remote_without_auth=serve_args.allow_remote_without_auth,
    )
    httpd = ThreadingHTTPServer(
        (serve_args.host, serve_args.port),
        make_handler(state, security=security),
    )
    host, port = httpd.server_address
    print(f"AMC simulator server listening on http://{host}:{port}/debug")
    print(f"Command API: POST http://{host}:{port}/v1/commands")
    print(f"Kubeconfig: http://{host}:{port}/v1/kubeconfig")
    if security.auth_token:
        print("Bearer auth: enabled")
    elif not _is_loopback_bind_host(serve_args.host):
        print("WARNING: remote bind is running without bearer auth", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nAMC simulator server stopping", file=sys.stderr)
    finally:
        if generation_stop is not None:
            generation_stop.set()
        httpd.server_close()


def _generation_argv_without_otel(generate_argv: list[str]) -> list[str]:
    # Server mode owns OTEL streaming separately from one-shot artifact
    # generation. The generation pass still writes metrics/logs/traces, but
    # this override prevents legacy main() from streaming and blocking server
    # startup or the continuous generation loop.
    return [*generate_argv, "--otel-send", "none"]


def _start_continuous_generation(
    state: SimulationState,
    generate_argv: list[str],
    *,
    enabled: bool,
    interval_seconds: float,
    stream_otel: bool = False,
) -> threading.Event | None:
    with state.generation.lock:
        state.generation.enabled = enabled
        state.generation.interval_seconds = interval_seconds
        state.generation.thread = "not_started" if enabled else "disabled"
        state.generation.last_anomaly_count = len(state.anomaly_rows)
    if not enabled:
        return None
    if stream_otel:
        state.otel_status["thread"] = "waiting"
        state.otel_status["continuous"] = True

    stop_event = threading.Event()

    def _run() -> None:
        with state.generation.lock:
            state.generation.thread = "running"
        if stream_otel:
            _stream_current_otel_once(state, idle_thread_state="waiting")
        while not stop_event.wait(interval_seconds):
            _run_continuous_generation_once(state, generate_argv, stream_otel=stream_otel)
        with state.generation.lock:
            state.generation.thread = "stopped"
        if stream_otel:
            state.otel_status["thread"] = "stopped"

    thread = threading.Thread(target=_run, name="amc-continuous-generation", daemon=True)
    thread.start()
    return stop_event


def _run_continuous_generation_once(
    state: SimulationState,
    generate_argv: list[str],
    *,
    stream_otel: bool = False,
) -> None:
    with state.generation.lock:
        next_count = state.generation.generation_count + 1
        seed = int(getattr(state.args, "seed", 42)) + next_count
        state.generation.thread = "running"
        state.generation.last_started_at = _dt.datetime.now(_dt.timezone.utc).isoformat()
        state.generation.last_seed = seed
        state.generation.last_error = ""
    run_argv = [*_generation_argv_without_otel(generate_argv), "--seed", str(seed)]
    try:
        state.legacy.main(run_argv)
        rows = load_anomaly_rows(state.output_dir / "anomalies.csv")
        state.replace_generated_rows(rows)
    except SystemExit as exc:  # pragma: no cover - defensive background boundary
        _record_continuous_generation_failure(state, exc)
        return
    except Exception as exc:  # pragma: no cover - defensive background boundary
        _record_continuous_generation_failure(state, exc)
        return
    with state.generation.lock:
        state.generation.generation_count = next_count
        state.generation.last_completed_at = _dt.datetime.now(_dt.timezone.utc).isoformat()
        state.generation.last_anomaly_count = len(rows)
    if stream_otel:
        _stream_current_otel_once(state, idle_thread_state="waiting")


def _start_otel_background(state: SimulationState) -> None:
    args = state.args
    if not getattr(args, "otel_enabled", False):
        state.otel_status["thread"] = "disabled"
        return

    def _run() -> None:
        _stream_current_otel_once(state, idle_thread_state="completed")

    thread = threading.Thread(target=_run, name="amc-otel-stream", daemon=True)
    thread.start()


def _stream_current_otel_once(state: SimulationState, *, idle_thread_state: str) -> None:
    if not getattr(state.args, "otel_enabled", False):
        state.otel_status["thread"] = "disabled"
        return
    state.otel_status["thread"] = "running"
    state.otel_status["last_started_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
    try:
        _run_otel_streams(state)
    except Exception as exc:  # pragma: no cover - defensive thread boundary
        state.otel_status["thread"] = "failed"
        state.otel_status["error"] = str(exc)
        return
    state.otel_status["thread"] = idle_thread_state
    state.otel_status["last_completed_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
    state.otel_status["stream_batches"] = int(state.otel_status.get("stream_batches", 0)) + 1


def _run_otel_streams(state: SimulationState) -> None:
    args = state.args
    legacy = state.legacy
    endpoints = {
        "logs": args.otel_logs_endpoint,
        "metrics": args.otel_metrics_endpoint,
        "traces": args.otel_traces_endpoint,
    }
    signal_selection = getattr(args, "otel_signal_selection", None)
    if signal_selection is not None:
        signal_endpoints = {
            sig: (url if sig in signal_selection else None)
            for sig, url in endpoints.items()
        }
    else:
        signal_endpoints = endpoints
    auth_headers = {}
    for signal in ["logs", "metrics", "traces"]:
        token = getattr(args, f"otel_{signal}_auth_token")
        if token:
            auth_headers[signal] = {"Authorization": f"{args.otel_stream_auth_scheme} {token}"}

    if not args.otel_gauges_only and any(signal_endpoints.values()):
        sent = legacy.stream_otel_signals(
            signal_endpoints,
            state.generated_rows(),
            speedup=args.otel_stream_speedup,
            timeout_seconds=args.otel_stream_timeout_seconds,
            max_events=args.otel_stream_max_events,
            auth_headers=auth_headers,
            protocol=args.otel_stream_protocol,
            activity_log_path=args.otel_activity_log,
            verbose=args.otel_verbose,
        )
        state.otel_status["signal_events_sent"] = sent
    if args.otel_emit_gauges:
        component_csv_paths = {
            c: args.output_dir / f"{c}.csv" for c in sorted(args.components)
        }
        sent = legacy.stream_otel_gauges(
            component_csv_paths,
            endpoint=args.otel_metrics_endpoint,
            batch_seconds=args.otel_gauge_batch_seconds,
            metric_prefix=args.otel_gauge_metric_prefix,
            speedup=args.otel_stream_speedup,
            timeout_seconds=args.otel_stream_timeout_seconds,
            max_events=args.otel_stream_max_events,
            max_retries=3,
            auth_headers=auth_headers.get("metrics"),
            protocol=args.otel_stream_protocol,
            activity_log_path=args.otel_activity_log,
            verbose=args.otel_verbose,
            append_activity_log=not args.otel_gauges_only,
        )
        state.otel_status["gauge_requests_sent"] = sent


def start_test_server(
    state: SimulationState,
    security: ServerSecurityConfig | None = None,
) -> tuple[ThreadingHTTPServer, str]:
    """Start an ephemeral server for tests and return (server, base_url)."""

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state, security=security))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address
    return httpd, f"http://{host}:{port}"


def temp_output_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix="amc-server-"))
