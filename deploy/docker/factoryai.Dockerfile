# One image, two roles: the API and Celery worker Deployments in the Helm chart
# (Phase 14) both run this image, differing only in their `command` — `factoryai serve`
# vs `factoryai worker`. They need the identical dependency set anyway (the API serves
# inference directly, so it needs the same `ml` extras training does — see
# `PredictImage`'s use of `detector_factory`), so a split image would duplicate the
# expensive `anomalib`/`torch` install for no isolation benefit.
#
# This is a deliberate, additional deployment target alongside ADR-0013's host-based one,
# not a replacement for it: the host-based `factoryai serve`/`factoryai worker` remain how
# local `docker compose` development runs (fast iteration, no image rebuild per change).
# Kubernetes is a different environment with a different constraint — no host to run on —
# so it gets its own image, exactly the same reasoning `airflow.Dockerfile` already
# established for Airflow's own isolated venv.
FROM python:3.11-slim AS runtime

# `libgl1`/`libglib2.0-0`: opencv-python (an anomalib dependency) dynamically loads
# `libGL.so.1` at import time — the identical gap `airflow.Dockerfile` hit and fixed
# (ADR-0015), for the identical reason.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 factoryai
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

# CPU-only torch first, from PyTorch's own CPU wheel index, before the rest of the
# extras: this image only ever does CPU inference (`PredictImage`'s `detector_factory`),
# never GPU training, but pip's default index resolves a CUDA-enabled wheel bundling
# several nvidia-cu13* packages regardless — found live (ADR-0017) as the reason this
# image was 10.7GB on disk against 3.44GB of actual content, and later as the reason a
# real CD build failed outright with "no space left on device" on a GitHub Actions
# runner's default disk. The CPU wheel satisfies the exact same `torch>=2.2,<3` /
# `torchvision>=0.17,<1` constraints `pyproject.toml` declares, so the subsequent extras
# install below finds them already satisfied and never reaches for the default index.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu \
    "torch>=2.2,<3" "torchvision>=0.17,<1"

RUN pip install --no-cache-dir \
    ".[storage,imaging,versioning,ml,api,auth,worker,monitoring]"

# `Settings.config_dir` (`shared/config.py`) defaults to `REPO_ROOT / "configs"`, where
# `REPO_ROOT` is `__file__`-derived — correct for an editable host install (ADR-0013's own
# deployment target), meaningless once `factoryai` is a regular `site-packages` install
# with no `configs/` anywhere nearby. Copying it in and overriding `FACTORYAI_CONFIG_DIR`
# (already a plain env-var-overridable `Settings` field, no code change needed) is the same
# fix Phase 12 already applied to the identical class of bug in `bootstrap.container.
# _REPO_ROOT` (`FACTORYAI_REPO_ROOT`, ADR-0015) — found live here the same way, a pod that
# started but failed `/health/ready` with "category configuration not found".
COPY configs ./configs
ENV FACTORYAI_CONFIG_DIR=/app/configs

# `database/migrations` (Alembic) reads its target DSN from `get_settings().database.dsn`
# (`database/migrations/env.py`), the same env-driven `Settings` everything else uses — so
# the migration Job the Helm chart runs against this image needs nothing beyond this
# directory and the `POSTGRES_*` env vars every other container in the chart already gets.
COPY database ./database

USER factoryai
ENV API_HOST=0.0.0.0 \
    API_PORT=8000

EXPOSE 8000

ENTRYPOINT ["factoryai"]
CMD ["serve"]
