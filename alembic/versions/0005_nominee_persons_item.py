"""Add nominee_persons table; add item/item_url/is_shortlisted to nominees;
add url to persons.

Revision ID: 0005
Revises:     0004
Create Date: 2026-04-12

What this migration covers
--------------------------
1. nominees.item          — replaces the old "song" text field
   nominees.item_url      — replaces the old "song_url" text field
   nominees.is_shortlisted – flag used by promote-to-final logic
   NOTE: if your DB was created BEFORE the "song" columns ever existed,
   the add_column calls are safe (they are idempotent-ish via try/except).
   If song/song_url columns already exist they are renamed first.

2. persons.url            — optional hyperlink for each person

3. nominee_persons        — m2m bridge so one nominee can credit
                            multiple persons with an optional role label
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = inspect(bind)
    return column in {c["name"] for c in insp.get_columns(table)}


def _table_exists(table: str) -> bool:
    bind = op.get_bind()
    insp = inspect(bind)
    return table in insp.get_table_names()


# ---------------------------------------------------------------------------
# upgrade
# ---------------------------------------------------------------------------

def upgrade() -> None:

    # ------------------------------------------------------------------
    # 1. nominees: song -> item  (rename if old name exists)
    # ------------------------------------------------------------------
    if _column_exists('nominees', 'song'):
        # PostgreSQL: rename column
        op.execute('ALTER TABLE nominees RENAME COLUMN song TO item')
    elif not _column_exists('nominees', 'item'):
        op.add_column('nominees', sa.Column('item', sa.String(), nullable=True))

    if _column_exists('nominees', 'song_url'):
        op.execute('ALTER TABLE nominees RENAME COLUMN song_url TO item_url')
    elif not _column_exists('nominees', 'item_url'):
        op.add_column('nominees', sa.Column('item_url', sa.String(), nullable=True))

    # ------------------------------------------------------------------
    # 2. nominees: is_shortlisted
    # ------------------------------------------------------------------
    if not _column_exists('nominees', 'is_shortlisted'):
        op.add_column('nominees',
            sa.Column('is_shortlisted', sa.Boolean(),
                      nullable=False, server_default='false'))

    # ------------------------------------------------------------------
    # 3. persons: url
    # ------------------------------------------------------------------
    if not _column_exists('persons', 'url'):
        op.add_column('persons', sa.Column('url', sa.String(), nullable=True))

    # ------------------------------------------------------------------
    # 4. nominee_persons  (many-to-many nominees <-> persons)
    # ------------------------------------------------------------------
    if not _table_exists('nominee_persons'):
        op.create_table(
            'nominee_persons',
            sa.Column('id',         sa.Integer(), primary_key=True),
            sa.Column('nominee_id', sa.Integer(),
                      sa.ForeignKey('nominees.id', ondelete='CASCADE'),
                      nullable=False),
            sa.Column('person_id',  sa.Integer(),
                      sa.ForeignKey('persons.id',  ondelete='CASCADE'),
                      nullable=False),
            sa.Column('role', sa.String(), nullable=True),
            sa.UniqueConstraint('nominee_id', 'person_id', name='uq_nominee_person'),
        )


# ---------------------------------------------------------------------------
# downgrade
# ---------------------------------------------------------------------------

def downgrade() -> None:
    if _table_exists('nominee_persons'):
        op.drop_table('nominee_persons')

    if _column_exists('persons', 'url'):
        op.drop_column('persons', 'url')

    if _column_exists('nominees', 'is_shortlisted'):
        op.drop_column('nominees', 'is_shortlisted')

    # Rename item -> song on downgrade (best-effort)
    if _column_exists('nominees', 'item'):
        op.execute('ALTER TABLE nominees RENAME COLUMN item TO song')
    if _column_exists('nominees', 'item_url'):
        op.execute('ALTER TABLE nominees RENAME COLUMN item_url TO song_url')
