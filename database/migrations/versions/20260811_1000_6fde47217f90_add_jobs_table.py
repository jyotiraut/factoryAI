"""add jobs table

Revision ID: 6fde47217f90
Revises: a1c9e6f2b3d4
Create Date: 2026-08-11 10:00:00.000000

Adds what Phase 9 (background processing, ADR-0012) needs: the ``jobs`` table tracking
Celery task submissions, status, progress and results. Mirrors
``src/factoryai/infrastructure/persistence/orm.py``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "6fde47217f90"
down_revision: str | None = "a1c9e6f2b3d4"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_JOB_TYPES = ("bulk_inference", "retraining", "dataset_versioning", "drift_report")
_JOB_STATUSES = ("queued", "running", "succeeded", "failed")


def upgrade() -> None:
    """Create the ``jobs`` table."""
    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("submitted_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("progress_completed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("progress_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("result", postgresql.JSONB, nullable=True),
        sa.Column("error", sa.String(length=4000), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["submitted_by"], ["users.id"]),
        sa.UniqueConstraint("idempotency_key", name="uq_jobs_idempotency_key"),
        sa.CheckConstraint(
            "job_type IN (" + ", ".join(f"'{value}'" for value in _JOB_TYPES) + ")",
            name="ck_jobs_type",
        ),
        sa.CheckConstraint(
            "status IN (" + ", ".join(f"'{value}'" for value in _JOB_STATUSES) + ")",
            name="ck_jobs_status",
        ),
    )
    op.create_index("ix_jobs_status_created", "jobs", ["status", "created_at"])


def downgrade() -> None:
    """Drop the ``jobs`` table."""
    op.drop_index("ix_jobs_status_created", table_name="jobs")
    op.drop_table("jobs")
