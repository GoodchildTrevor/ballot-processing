"""add winners table

Revision ID: 0004_winners
Revises: 0003_voter_draft
Create Date: 2026-04-11
"""
from alembic import op
import sqlalchemy as sa

revision = '0004_winners'
down_revision = '0003_voter_draft'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'winners',
        sa.Column('id',            sa.Integer(),  nullable=False, primary_key=True),
        sa.Column('nomination_id', sa.Integer(),  sa.ForeignKey('nominations.id'), nullable=False),
        sa.Column('nominee_id',    sa.Integer(),  sa.ForeignKey('nominees.id'),    nullable=True),
        sa.Column('announced_at',  sa.DateTime(), nullable=True),
        sa.Column('is_public',     sa.Boolean(),  nullable=False, server_default=sa.text('false')),
        sa.UniqueConstraint('nomination_id', name='uq_winner_nomination'),
    )


def downgrade() -> None:
    op.drop_table('winners')
