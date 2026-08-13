"""Prometheus collectors (Phase 7's predict-path metrics; Phase 11 expands the rest).

Kept separate from every router that touches it so none of them import each other.
Collectors fall into two groups: counters/histograms incremented as events happen
(predictions, HTTP requests), and gauges recomputed fresh on every scrape inside
``GET /metrics`` (system resources, queue depth, drift severity, cache hit rate) — the
latter group needs no background refresh loop precisely because Prometheus's own pull
model already re-reads them every scrape interval (ADR-0014).
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

PREDICTIONS_TOTAL = Counter(
    "factoryai_predictions_total",
    "Total predictions served, by category and verdict.",
    ["category", "is_anomalous"],
)

PREDICTION_LATENCY_SECONDS = Histogram(
    "factoryai_prediction_latency_seconds",
    "Inference latency per prediction, by category.",
    ["category"],
)

HTTP_REQUESTS_TOTAL = Counter(
    "factoryai_http_requests_total",
    "Total HTTP requests, by method, route template and status code.",
    ["method", "path", "status_code"],
)

HTTP_REQUEST_LATENCY_SECONDS = Histogram(
    "factoryai_http_request_latency_seconds",
    "Request latency, by method and route template.",
    ["method", "path"],
)

MODEL_CACHE_HIT_RATIO = Gauge(
    "factoryai_model_cache_hit_ratio",
    "Fraction of ModelCache.get() calls served from an already-loaded detector.",
)

JOBS_BY_STATUS = Gauge(
    "factoryai_jobs",
    "Background jobs currently in each status.",
    ["status"],
)

SYSTEM_CPU_PERCENT = Gauge("factoryai_system_cpu_percent", "Process host CPU utilisation.")
SYSTEM_MEMORY_PERCENT = Gauge("factoryai_system_memory_percent", "Process host memory utilisation.")
SYSTEM_DISK_PERCENT = Gauge("factoryai_system_disk_percent", "Process host root-volume disk usage.")
SYSTEM_GPU_UTILIZATION_PERCENT = Gauge(
    "factoryai_system_gpu_utilization_percent",
    "GPU utilisation, by device index. Absent entirely on a CPU-only host.",
    ["device"],
)

DRIFT_SEVERITY = Gauge(
    "factoryai_drift_severity",
    "Most recent drift report's severity for a category's production model: "
    "0=none, 1=low, 2=medium, 3=high.",
    ["category"],
)

DRIFT_SIGNAL_STATISTIC = Gauge(
    "factoryai_drift_signal_statistic",
    "Most recent drift report's per-signal statistic.",
    ["category", "signal"],
)

DRIFT_SIGNAL_BREACHED = Gauge(
    "factoryai_drift_signal_breached",
    "Whether the most recent drift report's signal breached its threshold (1) or not (0).",
    ["category", "signal"],
)

_SEVERITY_VALUES = {"none": 0, "low": 1, "medium": 2, "high": 3}
