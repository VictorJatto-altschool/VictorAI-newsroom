"""Database engine and session. SQLite by default, Postgres when DATABASE_URL is set."""
from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import DATA_DIR


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def init_engine(database_url: str = "") -> None:
    global _engine, _SessionLocal
    if not database_url:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        database_url = f"sqlite:///{(DATA_DIR / 'victor.db').as_posix()}"
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql+psycopg://", 1)
    elif database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    _engine = create_engine(database_url, connect_args=connect_args, future=True)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    from . import models  # noqa: F401  (register tables)
    Base.metadata.create_all(_engine)
    _ensure_columns(_engine)


# Columns added after the first release. create_all never alters existing tables, so add them here.
_ADDED_COLUMNS = {
    "posts": {"cost_usd": "FLOAT DEFAULT 0.0"},
    "drafts": {"take": "TEXT DEFAULT ''", "suggested_take": "TEXT DEFAULT ''"},
}


def _ensure_columns(engine) -> None:
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    with engine.begin() as conn:
        for table, cols in _ADDED_COLUMNS.items():
            if table not in insp.get_table_names():
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            for name, ddl in cols.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


@contextmanager
def session() -> Iterator[Session]:
    if _SessionLocal is None:
        init_engine()
    assert _SessionLocal is not None
    s = _SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
