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


# Columns added after initial deploy — safe to run every startup.
# SQLite does not support IF NOT EXISTS in ALTER TABLE, so we catch OperationalError.
_MIGRATIONS = [
    "ALTER TABLE nominations ADD COLUMN nominees_count INTEGER",
    "ALTER TABLE voters ADD COLUMN draft JSON",
    "ALTER TABLE nominees ADD COLUMN song TEXT",
]


def run_migrations():
    with engine.connect() as conn:
        for stmt in _MIGRATIONS:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception:
                # Column already exists — safe to ignore
                pass
