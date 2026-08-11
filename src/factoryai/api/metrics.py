"""Prometheus collectors shared between the predict and metrics routers.

Kept separate from both so neither imports the other.
"""

from __future__ import annotations

from prometheus_client import Counter, Histogram

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
