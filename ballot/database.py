import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

# Store DB in a dedicated directory so Docker volume mounts work correctly.
# A file-level volume mount (ballot.db) can block SQLite from creating the file;
# mounting a directory (data/) instead is always safe.
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
