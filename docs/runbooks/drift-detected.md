# Runbook: drift detected (`FactoryAIDriftMedium` / `FactoryAIDriftHigh`)

## What fired

`factoryai_drift_severity{category="..."}` has been at or above **medium** (2) for 10
minutes, or at **high** (3) for 5 minutes. This gauge reflects the most recently generated
`DriftReport` for that category's production model — see ADR-0014 and
`factoryai.application.use_cases.generate_drift_report`.

## What it means

The category's production model's recent predictions (`anomaly_score`, `confidence`) have
statistically diverged from that same model's earliest predictions by more than the
configured threshold (`DRIFT_DATA_THRESHOLD` / `DRIFT_PREDICTION_THRESHOLD`). This is a
signal about *production behaviour*, not automatically "the model is now wrong" — a
genuine shift in the physical product, lighting, or camera setup can move these
distributions just as easily as model degradation can.

## First steps

1. Open the **Model Quality** Grafana dashboard, filter to the affected category, and look
   at `factoryai_drift_signal_statistic` alongside `factoryai_drift_signal_breached` to see
   which signal(s) actually breached and by how much.
2. Check `GET /jobs` / the `monitoring` Airflow DAG's most recent run for the full report,
   including `sample_count` — a report generated on a small window is noisy; confirm
   `is_conclusive` was true.
3. Pull a handful of recent predictions for the category (`GET /models/{category}` and the
   `predictions` table) and eyeball a few images plus their heatmaps. Look for an obvious
   physical cause: a new product batch, a moved or dirty camera, a lighting change.
4. Check operator feedback (`Feedback` records) submitted in the same window — if
   operators are actively correcting the model, that is independent, stronger evidence
   than the statistical signal alone.

## Resolution

- **If a genuine cause is confirmed and correctable** (camera/lighting): fix the physical
  issue; drift severity should fall on its own once new predictions reflect the fix.
- **If the shift looks like real product/process drift**: trigger the `retraining` Airflow
  DAG (or `factoryai train` manually against a fresh dataset version) — Phase 12 will wire
  this to happen automatically from a high-severity alert; for now it is a manual step.
- **If the report was inconclusive or looks like noise**: no action — a report is not
  retrained on until `should_trigger_retraining` (severity medium or high) holds across
  more than one check.

## Escalation

This alert has no automated notification wired up yet (ADR-0014) — it is visible in
Alertmanager's own UI and in the `factoryai_drift_severity` gauge. Escalate manually to the
ML engineer on rotation.
