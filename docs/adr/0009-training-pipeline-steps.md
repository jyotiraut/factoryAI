# ADR-0009 — Training as a fixed sequence of single-responsibility steps

**Status:** accepted · **Date:** 2026-08-07

## Context

`factoryai train` must: resolve a dataset version to concrete files, stage them where an
`AnomalyDetector` can read them, fit and evaluate a model, record the run in MLflow, and
persist a `ModelVersion` — all as one reproducible unit, and all without the use case
itself knowing anything about Anomalib, MLflow, or the object store's key layout.

## Options considered

1. **One large `TrainModel.execute()` method.** Fewer files, but the method accumulates
   every concern (staging, fitting, logging, persisting) with no seam a test can target
   independently — exactly the shape `IngestImage` (Phase 3) and `CreateDatasetVersion`
   (Phase 4) deliberately avoided by keeping collaborators behind constructor-injected
   ports.
2. **A generic pipeline/step-runner abstraction** (a list of `Step` objects the use case
   iterates over). General, but training is not going to grow new steps at runtime — a
   generic runner would be solving a problem this use case does not have.
3. **A fixed sequence of named, single-responsibility collaborators, composed by one use
   case** — the same shape as `IngestImage`, just with more steps.

## Decision

Option 3. `TrainModel` orchestrates a fixed sequence — resolve the dataset version, stage
it to local disk, fit via the `AnomalyDetector` port (which internally covers build
datamodule → fit → evaluate, since Anomalib does not expose "evaluate" as a separable
call), log to MLflow via `ExperimentTracker`, register via `ModelRegistry`, persist an
`Experiment` and `ModelVersion` row, append an audit event — each concern behind its own
constructor-injected collaborator, exactly like `IngestImage` and `CreateDatasetVersion`.
The one step broken out as its own class rather than a private method is dataset staging
(`_DatasetStager`): it is the one piece with real, independently testable logic (fetching
member images from object storage and materialising them into the directory layout
`AnomalyDetector.fit` expects), whereas MLflow logging and row persistence are thin enough
to stay inline.

## Consequences

**Positive:** every collaborator (detector, tracker, registry, object store) is fakeable,
so the use case's control flow — what happens on a failed fit, what gets logged in what
order, what a compensating action looks like if registration fails after MLflow already
has the run — is unit-testable without a GPU, a running MLflow server, or real image
files. Adding a step later (e.g. drift-reference capture) is a new collaborator call, not
a rewrite.

**Negative:** the use case's constructor is another one with many parameters, matching
`IngestImage`'s and already covered by the `PLR0913` per-file ignore for
`factoryai.application.use_cases` (ADR-0001's stated trade-off, not a new one).
