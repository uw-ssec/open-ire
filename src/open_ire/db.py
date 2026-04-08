"""Database initialization and migration utilities."""

import logging
import threading
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, event
from sqlmodel import SQLModel, create_engine

logger = logging.getLogger(__name__)

_ALEMBIC_DIR = Path(__file__).resolve().parent / "migrations"
_migration_lock = threading.Lock()
_migrated_paths: set[str] = set()


def get_alembic_config(db_url: str) -> Config:
    """Build an Alembic Config pointing at the migration scripts."""
    cfg = Config()
    cfg.set_main_option("script_location", str(_ALEMBIC_DIR))
    cfg.attributes["sqlalchemy.url"] = db_url
    return cfg


def create_db_engine(db_path: str) -> Engine:
    """Create a SQLite engine with FK enforcement and run Alembic migrations.

    For in-memory databases (used in tests), falls back to ``create_all()``
    since Alembic migrations require a persistent connection.

    Parameters
    ----------
    db_path
        Path to the SQLite database file, or ``:memory:`` for in-memory.
    """
    is_memory = db_path == ":memory:"

    if not is_memory:
        parent_dir = Path(db_path).parent
        if not parent_dir.exists():
            parent_dir.mkdir(parents=True, exist_ok=True)
            logger.info("Created database directory at %s", parent_dir)

    db_url = f"sqlite:///{db_path}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})

    event.listen(
        engine,
        "connect",
        lambda dbapi_connection, _: dbapi_connection.execute("PRAGMA foreign_keys=ON"),
    )

    if is_memory:
        SQLModel.metadata.create_all(engine)
    else:
        canonical = str(Path(db_path).resolve())
        with _migration_lock:
            if canonical not in _migrated_paths:
                cfg = get_alembic_config(db_url)
                command.upgrade(cfg, "head")
                _migrated_paths.add(canonical)

    return engine
