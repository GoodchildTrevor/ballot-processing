import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase

DB_DIR = os.getenv("DB_DIR", "/app/data")
os.makedirs(DB_DIR, exist_ok=True)

DATABASE_URL = f"sqlite:///{DB_DIR}/ballot.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Columns/tables added after initial deploy — safe to run every startup.
# SQLite does not support IF NOT EXISTS in ALTER TABLE, so we catch OperationalError.
_MIGRATIONS = [
    # --- original ---
    "ALTER TABLE nominations ADD COLUMN nominees_count INTEGER",
    "ALTER TABLE voters ADD COLUMN draft JSON",
    "ALTER TABLE nominees ADD COLUMN song TEXT",
    "ALTER TABLE films ADD COLUMN url TEXT",
    "ALTER TABLE persons ADD COLUMN url TEXT",
    "ALTER TABLE nominees ADD COLUMN song_url TEXT",
    # --- deadline per nomination ---
    "ALTER TABLE nominations ADD COLUMN vote_deadline DATETIME",
    # --- rename song->item (additive: new columns, old kept for read-compat) ---
    "ALTER TABLE nominees ADD COLUMN item TEXT",
    "ALTER TABLE nominees ADD COLUMN item_url TEXT",
    # backfill new columns from old ones in SQLite
    "UPDATE nominees SET item = song WHERE item IS NULL AND song IS NOT NULL",
    "UPDATE nominees SET item_url = song_url WHERE item_url IS NULL AND song_url IS NOT NULL",
    # --- multi-person bridge table ---
    """
    CREATE TABLE IF NOT EXISTS nominee_persons (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nominee_id INTEGER NOT NULL REFERENCES nominees(id),
        person_id  INTEGER NOT NULL REFERENCES persons(id),
        role       TEXT,
        UNIQUE(nominee_id, person_id)
    )
    """,

    # ================================================================
    # Round system (added 2026-04)
    # ================================================================

    # rounds table
    """
    CREATE TABLE IF NOT EXISTS rounds (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        label      TEXT    NOT NULL,
        round_type TEXT    NOT NULL DEFAULT 'LONGLIST',
        year       INTEGER NOT NULL,
        deadline   DATETIME,
        is_active  INTEGER NOT NULL DEFAULT 0,
        sort_order INTEGER NOT NULL DEFAULT 0
    )
    """,

    # round_participations table (replaces per-voter voted_at/draft)
    """
    CREATE TABLE IF NOT EXISTS round_participations (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        round_id INTEGER NOT NULL REFERENCES rounds(id),
        voter_id INTEGER NOT NULL REFERENCES voters(id),
        voted_at DATETIME,
        draft    JSON,
        UNIQUE(round_id, voter_id)
    )
    """,

    # nominations.round_id FK
    "ALTER TABLE nominations ADD COLUMN round_id INTEGER REFERENCES rounds(id)",

    # nominations.has_runner_up
    "ALTER TABLE nominations ADD COLUMN has_runner_up INTEGER NOT NULL DEFAULT 0",

    # nominees.is_shortlisted
    "ALTER TABLE nominees ADD COLUMN is_shortlisted INTEGER NOT NULL DEFAULT 0",

    # votes.is_runner_up
    "ALTER TABLE votes ADD COLUMN is_runner_up INTEGER NOT NULL DEFAULT 0",

    # ================================================================
    # Contest + NominationTemplate system (added 2026-04-12)
    # ================================================================

    # contests table
    """
    CREATE TABLE IF NOT EXISTS contests (
        id     INTEGER PRIMARY KEY AUTOINCREMENT,
        year   INTEGER NOT NULL UNIQUE,
        name   TEXT    NOT NULL,
        status TEXT    NOT NULL DEFAULT 'DRAFT'
    )
    """,

    # nomination_templates table (without longlist_nominees_count)
    """
    CREATE TABLE IF NOT EXISTS nomination_templates (
        id                   INTEGER PRIMARY KEY AUTOINCREMENT,
        name                 TEXT    NOT NULL,
        description          TEXT,
        type                 TEXT    NOT NULL,
        sort_order           INTEGER NOT NULL DEFAULT 0,
        is_archived          INTEGER NOT NULL DEFAULT 0,
        longlist_pick_min    INTEGER,
        longlist_pick_max    INTEGER,
        final_promotes_count INTEGER
    )
    """,

    # contest_nominations join table
    """
    CREATE TABLE IF NOT EXISTS contest_nominations (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        contest_id  INTEGER NOT NULL REFERENCES contests(id),
        template_id INTEGER NOT NULL REFERENCES nomination_templates(id),
        sort_order  INTEGER NOT NULL DEFAULT 0,
        UNIQUE(contest_id, template_id)
    )
    """,

    # rounds.contest_id + rounds.tour
    "ALTER TABLE rounds ADD COLUMN contest_id INTEGER REFERENCES contests(id)",
    "ALTER TABLE rounds ADD COLUMN tour INTEGER NOT NULL DEFAULT 1",

    # nominations.contest_nomination_id
    "ALTER TABLE nominations ADD COLUMN contest_nomination_id INTEGER REFERENCES contest_nominations(id)",

    # ================================================================
    # Drop redundant longlist_nominees_count (added 2026-04-12)
    # SQLite >= 3.35.0 supports DROP COLUMN
    # ================================================================
    "ALTER TABLE nomination_templates DROP COLUMN longlist_nominees_count",
]


def run_migrations():
    with engine.connect() as conn:
        for stmt in _MIGRATIONS:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception:
                # Column/table already exists or doesn't exist — safe to ignore
                pass
