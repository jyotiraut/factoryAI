"""MLflow-backed experiment tracking (ADR-0004)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import mlflow.exceptions
import requests
from mlflow.tracking import MlflowClient

from factoryai.domain.entities import EvaluationMetrics
from factoryai.domain.ports.tracking import ExperimentTracker
from factoryai.shared.errors import InfrastructureError

_MLFLOW_ERRORS = (mlflow.exceptions.MlflowException, requests.exceptions.RequestException)


class MlflowExperimentTracker(ExperimentTracker):
    """Records runs against a self-hosted MLflow tracking server."""

    def __init__(self, tracking_uri: str) -> None:
        """Initialise a client against ``tracking_uri`` — no server round trip happens yet."""
        self._client = MlflowClient(tracking_uri=tracking_uri)

    def start_run(self, *, experiment_name: str, run_name: str) -> str:
        """Begin a run under ``experiment_name``, creating the experiment on first use.

        Raises:
            InfrastructureError: If the tracking server is unreachable or rejects the call.
        """
        try:
            experiment = self._client.get_experiment_by_name(experiment_name)
            experiment_id = (
                experiment.experiment_id
                if experiment is not None
                else self._client.create_experiment(experiment_name)
            )
            run = self._client.create_run(experiment_id=experiment_id, run_name=run_name)
        except _MLFLOW_ERRORS as exc:
            raise self._wrap(exc, f"starting run {run_name!r}") from exc
        return str(run.info.run_id)

    def log_params(self, run_id: str, params: dict[str, Any]) -> None:
        """Record immutable run inputs.

        Raises:
            InfrastructureError: If the tracking server is unreachable or rejects the call.
        """
        try:
            for key, value in params.items():
                self._client.log_param(run_id, key, value)
        except _MLFLOW_ERRORS as exc:
            raise self._wrap(exc, f"logging params for run {run_id!r}") from exc

    def log_metrics(
        self, run_id: str, metrics: dict[str, float], *, step: int | None = None
    ) -> None:
        """Record numeric results.

        Raises:
            InfrastructureError: If the tracking server is unreachable or rejects the call.
        """
        try:
            for key, value in metrics.items():
                self._client.log_metric(run_id, key, value, step=step)
        except _MLFLOW_ERRORS as exc:
            raise self._wrap(exc, f"logging metrics for run {run_id!r}") from exc

    def log_evaluation(self, run_id: str, metrics: EvaluationMetrics) -> None:
        """Record a complete evaluation.

        The confusion matrix is not itself a scalar, so its four counts are expanded into
        individual metrics (``confusion_matrix_tn`` etc.) rather than dropped.

        Raises:
            InfrastructureError: If the tracking server is unreachable or rejects the call.
        """
        payload = metrics.to_dict()
        confusion_matrix = payload.pop("confusion_matrix", None)
        scalars = {key: float(value) for key, value in payload.items()}
        if confusion_matrix is not None:
            true_negative, false_positive, false_negative, true_positive = confusion_matrix
            scalars.update(
                {
                    "confusion_matrix_tn": float(true_negative),
                    "confusion_matrix_fp": float(false_positive),
                    "confusion_matrix_fn": float(false_negative),
                    "confusion_matrix_tp": float(true_positive),
                }
            )
        self.log_metrics(run_id, scalars)

    def log_artifact(self, run_id: str, path: Path, *, artifact_path: str | None = None) -> None:
        """Attach a file to a run.

        Raises:
            InfrastructureError: If the tracking server is unreachable or rejects the call.
        """
        try:
            self._client.log_artifact(run_id, str(path), artifact_path)
        except _MLFLOW_ERRORS as exc:
            raise self._wrap(exc, f"logging artifact {path} for run {run_id!r}") from exc

    def end_run(self, run_id: str, *, status: str = "FINISHED") -> None:
        """Close a run.

        Raises:
            InfrastructureError: If the tracking server is unreachable or rejects the call.
        """
        try:
            self._client.set_terminated(run_id, status)
        except _MLFLOW_ERRORS as exc:
            raise self._wrap(exc, f"ending run {run_id!r}") from exc

    def _wrap(self, exc: Exception, action: str) -> InfrastructureError:
        """Translate an MLflow client error into the shared infrastructure error hierarchy."""
        return InfrastructureError(f"MLflow tracking failed while {action}: {exc}")
