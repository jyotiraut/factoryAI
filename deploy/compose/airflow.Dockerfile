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
RUN /opt/factoryai-venv/bin/pip install --no-cache-dir \
    "/tmp/factoryai[storage,imaging,versioning,ml,auth]"
