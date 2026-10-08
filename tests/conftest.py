from __future__ import annotations

from datetime import datetime, timezone

import pytest

from victor import db
from victor.config import CONFIG_DIR, Env, load_settings


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def settings():
    env = Env(app_env="development")
    return load_settings(CONFIG_DIR, env=env)


@pytest.fixture
def fresh_db(tmp_path):
    db.init_engine(f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    yield
    db._engine.dispose()
    db._engine = None
    db._SessionLocal = None
