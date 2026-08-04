# ADR-0004 — MLflow for experiment tracking and model registry

**Status:** accepted · **Date:** 2026-08-04

## Context

Every trained model must be traceable to its dataset version, Git commit, configuration and
hardware, and must move through lifecycle stages with the ability to roll back. Two
concerns — tracking and registry — could be served by one system or two.

## Options considered

1. **Weights & Biases** — excellent tracking UI, but SaaS-first; on-prem is an enterprise
   licence. A factory-floor deployment is often air-gapped.
2. **DVC experiments + Git tags as the registry** — no extra service, but no stage
   transitions, no comparison UI, and promotion becomes a manual Git ritual.
3. **MLflow for both.**
4. **MLflow for tracking + a bespoke registry in PostgreSQL.**

## Decision

MLflow for both, behind two ports: `ExperimentTracker` and `ModelRegistry`. MLflow runs
self-hosted with PostgreSQL as the backend store and MinIO as the artifact store.

Model lifecycle metadata is **mirrored** into the `model_versions` and `deployments` tables
(ADR: see DATA_MODEL §2). The dashboard and the promotion gate read from PostgreSQL; MLflow
is the artifact authority.

## Consequences

**Positive:** one service instead of two; open source and self-hostable, so air-gapped
deployment is possible; stage transitions and rollback come for free; the ports mean
replacing MLflow with SageMaker or Vertex registries later is an adapter swap.

**Negative:** MLflow becomes a hard dependency of the training path and a single point of
failure — mitigated by mirroring metadata into PostgreSQL so the dashboard degrades
gracefully. MLflow's stage model is coarse (no per-environment stages); the `deployments`
table carries the finer environment detail. Its auth story is weak, so it sits behind the
platform's own ingress and is never exposed directly.
