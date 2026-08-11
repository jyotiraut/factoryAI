"""Keeps one warmed detector per category, reloaded only when production actually changes.

Hot-reload without a restart falls out of a simpler property than a background poller: the
inference path already reads the current production ``ModelVersion`` from PostgreSQL
(ADR-0004: PostgreSQL is authoritative for stage decisions) on every request to build the
prediction it needs anyway. Comparing that row's id against what is already loaded is
free — no separate polling loop, no MLflow round trip on the hot path.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from factoryai.domain.ports.detection import AnomalyDetector
from factoryai.domain.ports.tracking import ModelRegistry
from factoryai.domain.value_objects import Category, ModelVersionId


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    """One category's currently loaded detector."""

    model_version_id: ModelVersionId
    detector: AnomalyDetector


class ModelCache:
    """Serves a ready-to-predict detector per category, loading or reloading as needed."""

    def __init__(
        self,
        *,
        detector_factory: Callable[[str, str | None], AnomalyDetector],
        model_registry: ModelRegistry,
        workdir: Path,
    ) -> None:
        """Initialise with everything needed to build and load a detector on demand.

        Args:
            detector_factory: Builds a fresh, unloaded detector for ``(family, backbone)``.
            model_registry: Where a model version's artifact is downloaded from.
            workdir: Local scratch directory downloaded artifacts are staged under.
        """
        self._detector_factory = detector_factory
        self._model_registry = model_registry
        self._workdir = workdir
        self._entries: dict[Category, _CacheEntry] = {}
        self._locks: dict[Category, asyncio.Lock] = {}

    async def get(
        self,
        category: Category,
        *,
        model_version_id: ModelVersionId,
        registry_name: str,
        registry_version: int,
        threshold: float,
        model_family: str,
        backbone: str,
    ) -> AnomalyDetector:
        """Return a detector loaded with the given model version.

        Reuses the cached instance when ``model_version_id`` matches what is already
        loaded for ``category``; otherwise downloads and loads the new version, replacing
        the cache entry. Concurrent calls for the same category serialise on a per-category
        lock, so a promotion landing mid-traffic triggers exactly one reload, not one per
        in-flight request.
        """
        lock = self._locks.setdefault(category, asyncio.Lock())
        async with lock:
            cached = self._entries.get(category)
            if cached is not None and cached.model_version_id == model_version_id:
                return cached.detector

            detector = await asyncio.to_thread(
                self._load,
                model_version_id=model_version_id,
                registry_name=registry_name,
                registry_version=registry_version,
                threshold=threshold,
                model_family=model_family,
                backbone=backbone,
            )
            self._entries[category] = _CacheEntry(
                model_version_id=model_version_id, detector=detector
            )
            return detector

    def _load(
        self,
        *,
        model_version_id: ModelVersionId,
        registry_name: str,
        registry_version: int,
        threshold: float,
        model_family: str,
        backbone: str,
    ) -> AnomalyDetector:
        """Download the artifact and load it into a fresh detector. Runs off the event loop."""
        destination = self._workdir / str(model_version_id)
        destination.mkdir(parents=True, exist_ok=True)
        artifact_path = self._model_registry.download(
            name=registry_name, version=registry_version, destination=destination
        )
        detector = self._detector_factory(model_family, backbone)
        detector.load(artifact_path, threshold=threshold)
        return detector

    def loaded_categories(self) -> tuple[Category, ...]:
        """Return every category with a detector currently warmed in this cache."""
        return tuple(self._entries)
