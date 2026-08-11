"""add auth tables

Revision ID: a1c9e6f2b3d4
Revises: 998632c10c0e
Create Date: 2026-08-07 09:00:00.000000

Adds what Phase 8 (JWT authentication, ADR-0011) needs beyond the initial schema:
``users.password_hash`` and the ``revoked_tokens`` blacklist. Mirrors
``src/factoryai/infrastructure/persistence/orm.py``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1c9e6f2b3d4"
down_revision: str | None = "998632c10c0e"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Add ``users.password_hash`` and create ``revoked_tokens``."""
    op.add_column("users", sa.Column("password_hash", sa.String(length=255), nullable=True))
    op.create_table(
        "revoked_tokens",
        sa.Column("jti", sa.String(length=64), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("jti"),
    )


def downgrade() -> None:
    """Drop ``revoked_tokens`` and ``users.password_hash``."""
    op.drop_table("revoked_tokens")
    op.drop_column("users", "password_hash")
