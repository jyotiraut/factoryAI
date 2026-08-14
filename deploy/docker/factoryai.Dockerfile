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

RUN pip install --no-cache-dir \
    ".[storage,imaging,versioning,ml,api,auth,worker,monitoring]"

USER factoryai
ENV API_HOST=0.0.0.0 \
    API_PORT=8000

EXPOSE 8000

ENTRYPOINT ["factoryai"]
CMD ["serve"]
