# ADR-0001 — Clean Architecture with ports and adapters

**Status:** accepted · **Date:** 2026-08-04

## Context

The platform must survive several predictable changes: MinIO → S3 when the plant moves to
cloud, PatchCore → a newer detector, MLflow → a managed registry, and one MVTec category →
many. A conventional layout where FastAPI routes call SQLAlchemy sessions and Anomalib
directly makes each of those a cross-cutting rewrite, and makes unit testing require a
running database.

## Options considered

1. **Flat FastAPI application** — routers, models, services in one package. Fastest to
   write, hardest to change. Business rules end up in HTTP handlers.
2. **Layered (n-tier)** — presentation → service → data access. Better, but the data-access
   layer still leaks: services import ORM models, so the schema shapes the business logic.
3. **Clean Architecture / ports and adapters** — dependencies point inwards, infrastructure
   implements interfaces owned by the domain.

## Decision

Option 3. Four layers: `domain`, `application`, `infrastructure`, presentation
(`services/`). The domain depends on nothing external. ORM models are separate from domain
entities, with explicit mappers.

Layering is enforced in CI by `import-linter` contracts, not by convention.

## Consequences

**Positive:** infrastructure is swappable; the domain is unit-testable in milliseconds with
no containers; the same use cases serve the API, CLI, Celery workers and Airflow DAGs
without duplication; new engineers have one obvious place for each kind of code.

**Negative:** more files and mapper boilerplate for simple CRUD; a genuine learning curve;
the temptation to leak an ORM model into a use case must be actively policed — hence the
CI contract.

**Accepted cost:** roughly 20–30% more code than a flat app, in exchange for the
swappability that is the point of this project.
