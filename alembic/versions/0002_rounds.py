"""Add rounds and round_participations tables; add nominees_count to nominations.

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-05
"""
from alembic import op
import sqlalchemy as sa


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- rounds table ---
    op.create_table(
        'rounds',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('label', sa.String(), nullable=False),
        sa.Column('round_type', sa.Enum('LONGLIST', 'FINAL', name='roundtype'),
                  nullable=False, server_default='LONGLIST'),
        sa.Column('year', sa.Integer(), nullable=False),
        sa.Column('deadline', sa.DateTime(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
    )

    # --- round_participations table ---
    op.create_table(
        'round_participations',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('round_id', sa.Integer(),
                  sa.ForeignKey('rounds.id'), nullable=False),
        sa.Column('voter_id', sa.Integer(),
                  sa.ForeignKey('voters.id'), nullable=False),
        sa.Column('voted_at', sa.DateTime(), nullable=True),
        sa.UniqueConstraint('round_id', 'voter_id', name='uq_round_voter'),
    )

    # --- nominations.round_id ---
    op.add_column('nominations',
        sa.Column('round_id', sa.Integer(),
                  sa.ForeignKey('rounds.id'), nullable=True))

    # --- nominations.nominees_count ---
    op.add_column('nominations',
        sa.Column('nominees_count', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('nominations', 'nominees_count')
    op.drop_column('nominations', 'round_id')
    op.drop_table('round_participations')
    op.drop_table('rounds')
    op.execute("DROP TYPE IF EXISTS roundtype")