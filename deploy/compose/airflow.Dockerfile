# Airflow's own official image has no `factoryai` package inside it — and it cannot get
# one installed into its own Python environment: every Airflow 2.x release pins
# `SQLAlchemy==1.4.54` in its published constraints file, while this platform requires
# `sqlalchemy>=2.0` (discovered by actually trying to build this image and hitting pip's
# `ResolutionImpossible`, not predicted in the abstract — see ADR-0013's "Consequences").
#
# The fix is a second, completely independent virtualenv baked into the same image,
# `/opt/factoryai-venv`, built with no constraint file at all — free to resolve modern
# SQLAlchemy, NumPy and everything else `factoryai[storage,imaging,versioning,ml]` needs.
# DAG tasks never import `factoryai` in Airflow's own process; they shell out to this
# interpreter running `factoryai.pipeline_runner` (see `pipelines/airflow/dags/common.py`).
FROM apache/airflow:2.10.4-python3.11

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3-venv \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/factoryai-venv \
    && chown -R airflow:root /opt/factoryai-venv

USER airflow

COPY --chown=airflow:root pyproject.toml README.md /tmp/factoryai/
COPY --chown=airflow:root src /tmp/factoryai/src

# `auth` is required even though no DAG task touches authentication: `bootstrap.container`
# imports `Argon2PasswordHasher`/`JwtTokenService` at module level (unlike the ML
# libraries, which it imports lazily inside cached_property methods — see that module's
# own docstring), so importing `Container` at all needs `argon2-cffi`/`pyjwt` installed.
# `monitoring` (evidently) is required for the same reason: `Container.drift_detector`
# imports `EvidentlyDriftDetector` lazily, but `monitoring_dag`'s own `check_drift` task
# always reaches it — found live (this session) via `check_drift` failing every single run
# with `ModuleNotFoundError: No module named 'evidently'`, never caught by any prior test
# since nothing exercises `pipeline_runner drift-report` through this specific venv.
RUN /opt/factoryai-venv/bin/pip install --no-cache-dir \
    "/tmp/factoryai[storage,imaging,versioning,ml,auth,monitoring]"

# `git` is required by `DvcGitVersionControl` (`current_commit`/`track_and_push` shell out
# to it) — placed in its own layer after the pip install rather than folded into the first
# `apt-get install` above, so adding it doesn't invalidate that layer's cache and force a
# full re-download of the ML dependencies. Both `/opt/factoryai-repo-src` (the host's bind
# mount `airflow-init` clones from) and `/opt/factoryai-repo` (the clone itself, in a
# container-only volume — see `docker-compose.yml`) are owned by a UID Git never considers
# "mine", so its dubious-ownership check needs a blanket `safe.directory` exemption or every
# `git`/`dvc` call in either one fails outright.
#
# `libgl1`/`libglib2.0-0` are required by `opencv-python`, a transitive dependency pulled in
# through `anomalib` (imported the moment training actually runs) — its compiled extension
# dynamically loads `libGL.so.1` at import time, which no Python-only fix can substitute for.
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends git libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system --add safe.directory '*'
USER airflow
