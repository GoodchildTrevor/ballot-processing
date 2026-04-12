"""Add Contest, NominationTemplate, ContestNomination; extend Round and Nomination.

Revision ID: 0004
Revises:     0003_voter_draft
Create Date: 2026-04-12
"""
from alembic import op
import sqlalchemy as sa

revision = '0004'
down_revision = '0003_voter_draft'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. contests
    # ------------------------------------------------------------------
    op.create_table(
        'contests',
        sa.Column('id',     sa.Integer(), primary_key=True),
        sa.Column('year',   sa.Integer(), nullable=False),
        sa.Column('name',   sa.String(),  nullable=False),
        sa.Column('status', sa.Enum(
            'DRAFT', 'LONGLIST_ACTIVE', 'LONGLIST_CLOSED',
            'FINAL_ACTIVE', 'FINAL_CLOSED',
            name='conteststatus'
        ), nullable=False, server_default='DRAFT'),
        sa.UniqueConstraint('year', name='uq_contest_year'),
    )

    # ------------------------------------------------------------------
    # 2. nomination_templates
    # ------------------------------------------------------------------
    op.create_table(
        'nomination_templates',
        sa.Column('id',          sa.Integer(), primary_key=True),
        sa.Column('name',        sa.String(),  nullable=False),
        sa.Column('description', sa.String(),  nullable=True),
        sa.Column('type', sa.Enum('RANK', 'PICK', name='nominationtype'),
                  nullable=False),
        sa.Column('sort_order',  sa.Integer(), nullable=False, server_default='0'),
        sa.Column('is_archived', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('longlist_nominees_count', sa.Integer(), nullable=True),
        sa.Column('longlist_pick_min',       sa.Integer(), nullable=True),
        sa.Column('longlist_pick_max',       sa.Integer(), nullable=True),
        sa.Column('final_promotes_count',    sa.Integer(), nullable=True),
    )

    # ------------------------------------------------------------------
    # 3. contest_nominations
    # ------------------------------------------------------------------
    op.create_table(
        'contest_nominations',
        sa.Column('id',          sa.Integer(), primary_key=True),
        sa.Column('contest_id',  sa.Integer(),
                  sa.ForeignKey('contests.id'), nullable=False),
        sa.Column('template_id', sa.Integer(),
                  sa.ForeignKey('nomination_templates.id'), nullable=False),
        sa.Column('sort_order',  sa.Integer(), nullable=False, server_default='0'),
        sa.UniqueConstraint('contest_id', 'template_id', name='uq_contest_template'),
    )

    # ------------------------------------------------------------------
    # 4. rounds — add contest_id + tour
    # ------------------------------------------------------------------
    op.add_column('rounds',
        sa.Column('contest_id', sa.Integer(),
                  sa.ForeignKey('contests.id'), nullable=True))
    op.add_column('rounds',
        sa.Column('tour', sa.Integer(), nullable=False, server_default='1'))

    # ------------------------------------------------------------------
    # 5. nominations — add contest_nomination_id
    # ------------------------------------------------------------------
    op.add_column('nominations',
        sa.Column('contest_nomination_id', sa.Integer(),
                  sa.ForeignKey('contest_nominations.id'), nullable=True))


def downgrade() -> None:
    op.drop_column('nominations', 'contest_nomination_id')
    op.drop_column('rounds', 'tour')
    op.drop_column('rounds', 'contest_id')
    op.drop_table('contest_nominations')
    op.drop_table('nomination_templates')
    op.drop_table('contests')
    op.execute("DROP TYPE IF EXISTS conteststatus")
    # nominationtype already existed — do not drop
