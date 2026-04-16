"""Initial schema with core tables.

Revision ID: 0001
Revises: None
Create Date: 2026-04-01
"""
from alembic import op
import sqlalchemy as sa


revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- voters ---
    op.create_table(
        'voters',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(), nullable=False, unique=True),
    )

    # --- films ---
    op.create_table(
        'films',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('title', sa.String(), nullable=False),
        sa.Column('year', sa.Integer(), nullable=False),
    )

    # --- persons ---
    op.create_table(
        'persons',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(), nullable=False),
    )

    # --- nominations ---
    op.create_table(
        'nominations',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('type', sa.Enum('RANK', 'PICK', name='nominationtype'),
                  nullable=False),
        sa.Column('pick_min', sa.Integer(), nullable=True),
        sa.Column('pick_max', sa.Integer(), nullable=True),
        sa.Column('year_filter', sa.Integer(), nullable=True),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
    )

    # --- nominees ---
    op.create_table(
        'nominees',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('nomination_id', sa.Integer(),
                  sa.ForeignKey('nominations.id'), nullable=False),
        sa.Column('film_id', sa.Integer(),
                  sa.ForeignKey('films.id'), nullable=False),
        sa.Column('person_id', sa.Integer(),
                  sa.ForeignKey('persons.id'), nullable=True),
    )

    # --- votes ---
    op.create_table(
        'votes',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('voter_id', sa.Integer(),
                  sa.ForeignKey('voters.id'), nullable=False),
        sa.Column('nominee_id', sa.Integer(),
                  sa.ForeignKey('nominees.id'), nullable=False),
        sa.UniqueConstraint('voter_id', 'nominee_id', name='uq_vote_voter_nominee'),
    )

    # --- rankings ---
    op.create_table(
        'rankings',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('voter_id', sa.Integer(),
                  sa.ForeignKey('voters.id'), nullable=False),
        sa.Column('nomination_id', sa.Integer(),
                  sa.ForeignKey('nominations.id'), nullable=False),
        sa.Column('film_id', sa.Integer(),
                  sa.ForeignKey('films.id'), nullable=False),
        sa.Column('rank', sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('rankings')
    op.drop_table('votes')
    op.drop_table('nominees')
    op.drop_table('nominations')
    op.drop_table('persons')
    op.drop_table('films')
    op.drop_table('voters')
    op.execute("DROP TYPE IF EXISTS nominationtype")