"""MLflow-backed model registry (ADR-0004)."""

from __future__ import annotations

from pathlib import Path

import mlflow.exceptions
import mlflow.tracking
import requests
from mlflow.tracking import MlflowClient

from factoryai.domain.ports.tracking import ModelRegistry
from factoryai.domain.value_objects import Category, ModelStage, StorageLocation
from factoryai.shared.errors import InfrastructureError

_MLFLOW_ERRORS = (mlflow.exceptions.MlflowException, requests.exceptions.RequestException)

_STAGE_NAMES: dict[ModelStage, str] = {
    ModelStage.DEVELOPMENT: "None",
    ModelStage.STAGING: "Staging",
    ModelStage.PRODUCTION: "Production",
    ModelStage.ARCHIVED: "Archived",
}
"""MLflow's built-in stage vocabulary has no "development" — an unassigned version is
``"None"``, which is exactly what a freshly registered, not-yet-promoted version is."""


class MlflowModelRegistry(ModelRegistry):
    """Registers and promotes model artifacts against a self-hosted MLflow server."""

    def __init__(self, tracking_uri: str) -> None:
        """Initialise a client against ``tracking_uri`` — no server round trip happens yet.

        Also sets MLflow's process-global tracking and registry URIs: ``mlflow.artifacts.
        download_artifacts`` resolves ``models:/`` URIs through that global state rather
        than through any URI passed to it directly, defaulting to a local ``./mlruns``
        file store otherwise — silently the wrong server for :meth:`download`.
        """
        self._client = MlflowClient(tracking_uri=tracking_uri)
        mlflow.tracking.set_tracking_uri(tracking_uri)
        mlflow.tracking.set_registry_uri(tracking_uri)

    def register(
        self,
        *,
        name: str,
        run_id: str,
        artifact_path: Path,
        tags: dict[str, str] | None = None,
    ) -> int:
        """Register the artifact a run already logged, creating the registry name if needed.

        Raises:
            InfrastructureError: If the registry server is unreachable or rejects the call.
        """
        try:
            self._ensure_registered_model(name)
            model_version = self._client.create_model_version(
                name=name,
                source=f"runs:/{run_id}/{artifact_path.as_posix()}",
                run_id=run_id,
                tags=tags,
            )
        except _MLFLOW_ERRORS as exc:
            raise self._wrap(exc, f"registering {name!r} from run {run_id!r}") from exc
        return int(model_version.version)

    def transition_stage(self, *, name: str, version: int, stage: ModelStage) -> None:
        """Move a registered version to a lifecycle stage.

        Raises:
            InfrastructureError: If the registry server is unreachable or rejects the call.
        """
        try:
            self._client.transition_model_version_stage(
                name=name, version=str(version), stage=_STAGE_NAMES[stage]
            )
        except _MLFLOW_ERRORS as exc:
            raise self._wrap(exc, f"transitioning {name!r} v{version} to {stage}") from exc

    def download(self, *, name: str, version: int, destination: Path) -> Path:
        """Fetch a version's artifact to local disk.

        Resolves through the owning run's artifacts rather than a ``models:/`` URI:
        ``mlflow.artifacts.download_artifacts`` resolves those through a *second*,
        independent URI-resolution path that does not consistently pick up this client's
        configured server, and 404s against the real object even though it exists.

        Raises:
            InfrastructureError: If the registry server is unreachable or rejects the call.
        """
        destination.mkdir(parents=True, exist_ok=True)
        try:
            model_version = self._client.get_model_version(name, str(version))
            artifact_subpath = model_version.source.removeprefix(f"runs:/{model_version.run_id}/")
            local_path = self._client.download_artifacts(
                model_version.run_id, artifact_subpath, str(destination)
            )
        except _MLFLOW_ERRORS as exc:
            raise self._wrap(exc, f"downloading {name!r} v{version}") from exc
        return Path(local_path)

    def get_stage_version(self, *, name: str, stage: ModelStage) -> int | None:
        """Return the version currently occupying a stage, if any.

        Raises:
            InfrastructureError: If the registry server is unreachable or rejects the call.
        """
        try:
            versions = self._client.get_latest_versions(name, stages=[_STAGE_NAMES[stage]])
        except _MLFLOW_ERRORS as exc:
            raise self._wrap(exc, f"reading the {stage} version of {name!r}") from exc
        return int(versions[0].version) if versions else None

    def list_versions(self, *, name: str) -> list[int]:
        """Return every registered version number, ascending.

        Raises:
            InfrastructureError: If the registry server is unreachable or rejects the call.
        """
        try:
            results = self._client.search_model_versions(f"name='{name}'")
        except _MLFLOW_ERRORS as exc:
            raise self._wrap(exc, f"listing versions of {name!r}") from exc
        return sorted(int(result.version) for result in results)

    def registry_name_for(self, category: Category) -> str:
        """Return one registry name per category, so stage transitions never cross categories."""
        return f"factoryai-{category.code}"

    def resolve_artifact_location(self, *, name: str, version: int) -> StorageLocation:
        """Return the S3/MinIO bucket and key MLflow actually wrote the artifact to.

        Raises:
            InfrastructureError: If the registry server is unreachable, or the artifact
                was not written to an S3-compatible store (unexpected in this deployment —
                ADR-0004 — and a bug elsewhere if it happens).
        """
        try:
            model_version = self._client.get_model_version(name, str(version))
            run = self._client.get_run(model_version.run_id)
        except _MLFLOW_ERRORS as exc:
            raise self._wrap(
                exc, f"resolving the artifact location of {name!r} v{version}"
            ) from exc
        artifact_subpath = model_version.source.removeprefix(f"runs:/{model_version.run_id}/")
        full_uri = f"{run.info.artifact_uri.rstrip('/')}/{artifact_subpath}"
        if not full_uri.startswith("s3://"):
            raise InfrastructureError(
                f"expected an s3:// artifact URI, got {full_uri!r}", details={"uri": full_uri}
            )
        bucket, _, key = full_uri.removeprefix("s3://").partition("/")
        return StorageLocation(bucket, key)

    def _ensure_registered_model(self, name: str) -> None:
        """Create the registry name if it does not already exist."""
        try:
            self._client.get_registered_model(name)
        except mlflow.exceptions.MlflowException:
            self._client.create_registered_model(name)

    def _wrap(self, exc: Exception, action: str) -> InfrastructureError:
        """Translate an MLflow client error into the shared infrastructure error hierarchy."""
        return InfrastructureError(f"MLflow registry failed while {action}: {exc}")
