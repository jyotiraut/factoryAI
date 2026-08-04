# ADR-0005 — Airflow for scheduled workflows, Celery for request-triggered work

**Status:** accepted · **Date:** 2026-08-04

## Context

Two different kinds of asynchronous work exist. One is scheduled, multi-step and
dependency-shaped: validate → version → train → evaluate → deploy → monitor, run nightly or
on a drift trigger. The other is request-triggered and latency-sensitive: a user uploads
500 images and wants a job id back immediately.

## Options considered

1. **Airflow for everything** — DAG scheduling latency (seconds to a minute) is wrong for
   interactive jobs, and dynamically creating a DAG run per API request is an anti-pattern.
2. **Celery for everything** — fine for tasks, poor for workflows: no dependency graph, no
   backfill, no retry semantics per step, no operational UI for pipeline state.
3. **Both, with a clear boundary.**

## Decision

Option 3, with the boundary stated explicitly:

| Use Airflow when | Use Celery when |
|---|---|
| The work is scheduled or event-triggered by the platform | The work originates from an HTTP request |
| It has multiple steps with dependencies | It is a single logical unit |
| Backfill, SLA and per-step retry matter | Sub-second dispatch matters |
| Examples: nightly validation, retraining, drift analysis | Examples: bulk inference, report generation, ad-hoc training |

Both call the *same* application use cases through a thin client. Airflow DAG files and
Celery task functions contain no business logic — a DAG task is a use-case invocation and
nothing else. This keeps the boundary a deployment concern rather than a code fork.

## Consequences

**Positive:** each tool used for what it is good at; logic lives in one place regardless of
trigger; Airflow's UI gives operators pipeline visibility that Celery cannot.

**Negative:** two async systems to operate, monitor and containerise — Redis for Celery,
plus Airflow's own metadata database and scheduler. This is real operational weight and is
justified only because both classes of work genuinely exist here. Correlation IDs must be
propagated through both to keep tracing coherent.
