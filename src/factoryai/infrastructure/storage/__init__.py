"""Object storage adapters implementing :class:`factoryai.domain.ports.storage.ObjectStore`.

Two adapters exist for Phase 2: :class:`.local.LocalObjectStore` (filesystem, for fast
unit tests with no Docker dependency) and :class:`.s3_compatible.S3CompatibleObjectStore`
(boto3 against MinIO or real AWS S3 — the two are the same wire protocol, so one adapter
serves both, selected by endpoint URL). Azure and GCS adapters follow when a phase
actually needs them (ADR-0003).
"""
