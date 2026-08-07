"""Experiment tracking and model registry ports (ADR-0004)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from factoryai.domain.entities import EvaluationMetrics
from factoryai.domain.value_objects import Category, ModelStage, StorageLocation


class ExperimentTracker(ABC):
    """Records the parameters, metrics and artifacts of a training run.

    Kept separate from :class:`ModelRegistry` even though MLflow provides both: tracking is
    write-heavy and tolerates being unavailable, whereas the registry sits on the serving
    path. Splitting them means an outage in one does not have to take out the other.
    """

    @abstractmethod
    def start_run(self, *, experiment_name: str, run_name: str) -> str:
        """Begin a run and return its tracking identifier."""

    @abstractmethod
    def log_params(self, run_id: str, params: dict[str, Any]) -> None:
        """Record immutable run inputs: hyperparameters, dataset version, Git commit."""

    @abstractmethod
    def log_metrics(
        self, run_id: str, metrics: dict[str, float], *, step: int | None = None
    ) -> None:
        """Record numeric results, optionally at a training step."""

    @abstractmethod
    def log_evaluation(self, run_id: str, metrics: EvaluationMetrics) -> None:
        """Record a complete evaluation, expanding optional metrics only when present."""

    @abstractmethod
    def log_artifact(self, run_id: str, path: Path, *, artifact_path: str | None = None) -> None:
        """Attach a file — weights, heatmaps, an evaluation report — to a run."""

    @abstractmethod
    def end_run(self, run_id: str, *, status: str = "FINISHED") -> None:
        """Close a run, marking its terminal status."""


class ModelRegistry(ABC):
    """Stores model artifacts and governs their lifecycle stages."""

    @abstractmethod
    def register(
        self,
        *,
        name: str,
        run_id: str,
        artifact_path: Path,
        tags: dict[str, str] | None = None,
    ) -> int:
        """Register an artifact under a registry name.

        Returns:
            The assigned registry version number.
        """

    @abstractmethod
    def transition_stage(self, *, name: str, version: int, stage: ModelStage) -> None:
        """Move a registered version to a lifecycle stage."""

    @abstractmethod
    def download(self, *, name: str, version: int, destination: Path) -> Path:
        """Fetch a version's artifact to local disk.

        Returns:
            The path the artifact was written to.
        """

    @abstractmethod
    def get_stage_version(self, *, name: str, stage: ModelStage) -> int | None:
        """Return the version currently occupying a stage, if any.

        The inference service polls this to detect a promotion and hot-reload without a
        restart.
        """

    @abstractmethod
    def list_versions(self, *, name: str) -> list[int]:
        """Return every registered version number, ascending."""

    @abstractmethod
    def registry_name_for(self, category: Category) -> str:
        """Return the registry name used for a category.

        Each category gets its own registry name, which is what keeps stage transitions
        for one product class from disturbing another.
        """

    @abstractmethod
    def resolve_artifact_location(self, *, name: str, version: int) -> StorageLocation:
        """Return where a registered version's artifact actually lives.

        The registry owns artifact placement (MLflow writes into its own bucket/key
        convention); this is what lets :class:`~factoryai.domain.entities.model.
        ModelVersion.artifact_location` record the real location instead of one the
        caller has to guess or reconstruct.
        """
