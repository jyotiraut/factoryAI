# ADR-0017 — Kubernetes, cloud and CI/CD hardening

**Status:** accepted · **Date:** 2026-08-17

## Context

Every prior phase's exit criterion was satisfiable on a single laptop — a `docker compose`
stack, a host-based `factoryai serve`. Phase 14's own exit criteria are explicitly about
leaving that laptop: `helm install factoryai` bringing up the full stack on a real
Kubernetes cluster, and a CI/CD pipeline that a HIGH/CRITICAL CVE or a failing test can
actually block. Given the size of this phase (new system-wide tooling: `kind`, `helm`,
`terraform`, `trivy`, `locust`), the user was asked how to proceed and explicitly chose to
install what's needed and live-verify — matching how every phase since Phase 10 has been
verified against real infrastructure, not just written and unit-tested.

## Decisions

**One image for both api and worker, not two.** The Celery worker's `run_bulk_inference`
task calls the exact same `detector_factory`/`ModelCache` machinery `PredictImage` uses, so
it needs the identical `ml` extras (`anomalib`, `torch`) the API does — a split image would
duplicate the single most expensive layer in the build for no isolation benefit. The two
Deployments differ only in `command`: `factoryai serve` vs `factoryai worker`.

**This is an additional deployment target, not a replacement for ADR-0013's host-based
one.** Local `docker compose` development still runs `factoryai serve`/`worker` directly on
the host for fast iteration without an image rebuild per change; Kubernetes is a different
environment with a different constraint (no host to run on), so it gets its own image —
the same reasoning `airflow.Dockerfile` already established for Airflow's own isolated venv.

**A post-install/post-upgrade Helm hook Job runs Alembic migrations, not an initContainer
or a manual step.** `database/migrations/env.py` already reads its target DSN dynamically
from `get_settings().database.dsn(...)`, the same env-driven `Settings` every other
container in the chart gets — so the migration Job needs nothing beyond the app's own image
and that env. `helm.sh/hook-weight: "-1"` runs it before the MinIO-bucket-creation and
MLflow-database-creation init Jobs, and before api/worker's own probes can pass, since
`/health/ready` genuinely depends on the schema existing.

**Bundled-vs-external endpoint resolution lives in `_helpers.tpl`, not duplicated per
template.** `factoryai.postgresHost`/`redisHost`/`storageEndpoint`/`mlflowUri` each check
`.enabled` on the corresponding bundled sub-chart values and fall back to a `required()`
external value — so `values.yaml` (dev: everything bundled) and `values-staging.yaml`/
`values-production.yaml` (everything external, `enabled: false`) are both valid without any
template-level branching duplicated across the ConfigMap, Deployments, and init Jobs that
all need the same hostnames.

**Terraform: cloud-agnostic modules, AWS as the reference implementation, no networking
provisioning.** `modules/object-storage`, `managed-postgres`, `container-registry`,
`secrets` each take a VPC/subnet ID as input rather than creating one — matching how most
organizations already have networking managed separately from application infrastructure,
and keeping each module's blast radius to exactly the resource it names.

**CI/CD: a Trivy image scan and an SBOM are gates in the pipeline, not a one-off audit
step.** `deploy/docker/factoryai.Dockerfile`'s built image is scanned with
`aquasecurity/trivy-action` at HIGH/CRITICAL severity with a nonzero exit code on any match
(the pipeline fails the build, not just reports it), and an SPDX SBOM is generated via
`anchore/sbom-action` on every build — both run before the image is ever pushed to
`ghcr.io`. Production deploys additionally require GitHub's own `environment: production`
required-reviewers gate — a repository setting, not something expressible in the workflow
YAML itself, which is why it is documented in comments rather than encoded as a job
condition.

## Consequences

- `deploy/docker/factoryai.Dockerfile`, `deploy/helm/factoryai/` (chart + two environment
  value overlays), `deploy/terraform/` (four modules + an AWS root module), `.github/
  workflows/cd.yml`, `deploy/loadtest/locustfile.py`, and `docs/CAPACITY.md` are all new.
- `ApiSettings.cors_origins` (`shared/config.py`) gained a `NoDecode` annotation — a real,
  previously-latent bug this phase's live verification found (see below), not new scope.
- Not built: a `HorizontalPodAutoscaler` for the worker Deployment (needs a custom-metrics
  adapter reading the existing Celery queue-depth gauge — cluster wiring out of this phase's
  scope, `docs/CAPACITY.md` documents the gap and the correct design), and an actually-run
  production deployment (`KUBE_CONFIG_STAGING`/`KUBE_CONFIG_PRODUCTION` secrets and real
  clusters do not exist in this environment — the workflow is written and YAML-validated,
  never executed end-to-end).

## Live verification

Every piece of new tooling this phase needed (`kind`, `helm`, `terraform`, `trivy`,
`locust`) was actually installed and actually run, per the user's explicit choice, rather
than left as an unverified writing exercise.

**Helm chart, on a real kind cluster — the full stack came up.** `helm lint` and `helm
template` (21 resources) passed cleanly, `kubectl apply --dry-run=server` validated all 21
against a real API server with zero schema errors, and a real `helm install` brought every
component (`factoryai-api` ×2, `factoryai-worker` ×2, `factoryai-postgresql`,
`factoryai-redis`, `factoryai-minio`, `factoryai-mlflow`) to `1/1 Running`. A real HTTP
round trip through `kubectl port-forward` confirmed:

```
{"status":"ok","checks":{}}                                            -- /health/live
{"status":"ok","checks":{"database":true,"model_registry":true}}       -- /health/ready
```

A real operator user was created (`kubectl exec ... factoryai user create --email
loadtest@factoryai.local --role operator ...`) and logged in successfully, returning real
JWT tokens — the exit criterion "`helm install factoryai` brings up the full stack on a
kind cluster" is satisfied by direct observation, not inference.

**Three real, previously-latent bugs were found and fixed this way** — none of which any
existing unit test could have caught, since each depends on behavior that only differs
between an editable host install / directly-constructed `Settings` object and a real
packaged container reading real env vars:

1. **`cors_origins` crash-looped both api and worker pods.** `ApiSettings.cors_origins:
   tuple[str, ...]` lacked the `NoDecode` annotation `IngestionSettings`'s own tuple fields
   already carry — pydantic-settings tried to JSON-decode the ConfigMap's plain
   comma-separated string before this class's own `mode="before"` validator ever ran,
   raising `ConfigurationError: invalid configuration: error parsing value for field
   "cors_origins"`. Every existing unit test constructs `ApiSettings` directly with a real
   Python tuple, so this path was never exercised through an actual env var until a real
   ConfigMap did exactly that. Fixed with the same `Annotated[..., NoDecode]` pattern
   already established elsewhere in the file, plus two regression tests
   (`TestApiSettings` in `tests/unit/shared/test_config.py`) asserting a comma-separated
   env var round-trips correctly.
2. **`/health/ready` failed with "category configuration not found."** `Settings.
   config_dir` defaults to a `__file__`-derived `REPO_ROOT / "configs"` — correct for
   ADR-0013's editable host install, meaningless once `factoryai` is a `pip install`-based
   `site-packages` package with no `configs/` nearby. The identical class of bug Phase 12
   already fixed for `bootstrap.container._REPO_ROOT` (`FACTORYAI_REPO_ROOT`, ADR-0015).
   Fixed with zero code changes — `config_dir` was already a plain `FACTORYAI_`-prefixed
   overridable `Settings` field — by adding `COPY configs ./configs` and `ENV
   FACTORYAI_CONFIG_DIR=/app/configs` to the Dockerfile (restated in the Helm ConfigMap).
3. **`/health/ready` then failed with `UndefinedTable: relation "model_versions" does not
   exist."`** The bundled Postgres was freshly created with no schema — nothing had ever
   automated running Alembic against it; the `Makefile`'s `migrate` target is a manual,
   host-only step. Fixed by copying `database/` into the image and adding `templates/
   migrate.yaml`, a hook-weight `-1` post-install/post-upgrade Job running `alembic -c
   database/alembic.ini upgrade head` — verified `env.py` already reads its DSN from the
   same `Settings` every other container gets, so no extra config was needed.

**Clean single-request latencies confirm real per-request cost; concurrent Locust numbers
do not.** Non-concurrent `curl` timing checks against the live cluster came back well
within budget: `/models` 22ms, `/predictions` 50ms, `/health/live` 346ms, `/health/ready`
112ms. Two Locust runs (10 users/45s, then 3 users/40s) against the same cluster through
`kubectl port-forward` both showed 83–94% failure rates — but the port-forward process's
own log, not the app's, recorded the actual cause: `"lost connection to pod"` /
`"connection reset by peer"`. `kubectl port-forward` is a single-TCP-tunnel debugging aid,
not a load-balanced data path, and is well known to drop under even light concurrency
independent of backend health. These numbers are recorded in `docs/CAPACITY.md` as
inconclusive test-harness noise, not as a finding about the application's real capacity —
a trustworthy concurrent load test needs to run from inside the cluster (a Locust pod
hitting the Service/Ingress directly) or through a real external ingress, tracked as
follow-up work.

**Terraform:** `terraform fmt -recursive` (one file auto-formatted) and `terraform init
-backend=false && terraform validate` passed cleanly for the root `environments/aws`
module and each of the four modules standalone — the verification ceiling reachable
without real AWS credentials, which do not exist in this environment. `terraform apply`
was never run.

**Trivy scan against the built image found real, disclosed findings — the CD pipeline's
own gate would currently fail this image.** `trivy image --severity HIGH,CRITICAL
--ignore-unfixed factoryai:local` reports 9 HIGH OS-package CVEs (Debian base image,
`bsdutils`/`util-linux`, fixed versions available upstream but not yet pulled) and, more
significantly, 32 Python-package findings (26 HIGH, 6 CRITICAL) — all 6 CRITICAL entries
are the same package, `mlflow==2.22.5` (CVE-2025-15036, CVE-2025-15379, CVE-2026-0596,
CVE-2026-2635, CVE-2026-2651, CVE-2026-4035), fixed only in `mlflow>=3.8`. This is a real,
actionable finding, not swept aside: the `cd.yml` workflow's Trivy step is configured with
`exit-code: 1` on HIGH/CRITICAL specifically so this class of finding blocks a real build —
which is exactly what it would do here. Upgrading past `mlflow` 2.x to 3.x is a real,
separately-scoped migration (a major-version bump with its own breaking changes across the
`experiment_tracking` integration) and is tracked as follow-up work rather than rushed into
this phase.

**The 10.7GB image is disk usage, not content size — `docker images` shows `3.44GB`
content vs `10.7GB` disk usage, with many `nvidia-cu13*` packages present despite this
image only ever serving CPU inference.** A GPU-enabled `torch` wheel was pulled by default;
pinning a CPU-only `torch` build (the standard `--index-url
https://download.pytorch.org/whl/cpu` approach) would shrink both image size and the CVE
surface implicated above (fewer packages, less to have vulnerabilities). Not fixed this
phase — flagged as a concrete, scoped optimization rather than left as an unexplained
number in a build log.
