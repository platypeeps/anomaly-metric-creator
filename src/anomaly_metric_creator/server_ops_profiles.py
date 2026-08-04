"""Ops scenario profile registry and its dataclasses/validator.

Pure-data leaf extracted from ``server_ops.py`` (epic step 1). Owns the
``OpsComponentImpact`` / ``OpsScenarioProfile`` dataclasses, the
``_impact`` / ``_profile`` builders, the ``OPS_SCENARIO_PROFILES`` registry,
and ``validate_ops_profiles``. ``server_ops.py`` re-imports every name here
at the original block position; this module never imports ``server_ops``
(one-way dependency).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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
