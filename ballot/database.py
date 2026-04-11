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
]


def run_migrations():
    with engine.connect() as conn:
        for stmt in _MIGRATIONS:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception:
                # Column/table already exists — safe to ignore
                pass
