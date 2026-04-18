"""Add acting_group to nominations

Revision ID: 0007
Revises: 0006
Create Date: 2026-04-18

Adds optional acting_group text column to nominations so per-contest override is possible.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = inspect(bind)
    return column in {c["name"] for c in insp.get_columns(table)}

def upgrade() -> None:
    if not _column_exists("nominations", "acting_group"):
        op.add_column(
            "nominations",
            sa.Column("acting_group", sa.String(), nullable=True),
        )

def downgrade() -> None:
    if _column_exists("nominations", "acting_group"):
        op.drop_column("nominations", "acting_group")