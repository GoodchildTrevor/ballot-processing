"""add draft column to voters

Revision ID: 0003_voter_draft
Revises: 0002
Create Date: 2026-04-08
"""
from alembic import op
import sqlalchemy as sa

revision = '0003_voter_draft'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('voters', sa.Column('draft', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('voters', 'draft')
