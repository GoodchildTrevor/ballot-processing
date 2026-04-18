"""Add acting_group to nomination_templates

Revision ID: 0006
Revises: 0005
Create Date: 2026-04-18

This migration adds the optional acting_group text column to the
nomination_templates table so templates can be grouped for merging votes.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = inspect(bind)
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    if not _column_exists("nomination_templates", "acting_group"):
        op.add_column(
            "nomination_templates",
            sa.Column("acting_group", sa.String(), nullable=True),
        )


def downgrade() -> None:
    if _column_exists("nomination_templates", "acting_group"):
        op.drop_column("nomination_templates", "acting_group")