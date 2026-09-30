"""Database initialization and migration utilities."""

import logging
import threading
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, event, inspect
from sqlmodel import SQLModel, create_engine

from open_ire.errors import DatabaseRevisionError

logger = logging.getLogger(__name__)

_ALEMBIC_DIR = Path(__file__).resolve().parent / "alembic"
_migration_lock = threading.Lock()
_verified_paths: set[str] = set()


def get_alembic_config(db_url: str) -> Config:
    """Build an Alembic Config pointing at the migration scripts."""
    cfg = Config()
    cfg.set_main_option("script_location", str(_ALEMBIC_DIR))
    cfg.attributes["sqlalchemy.url"] = db_url
    return cfg


def _upgrade_or_verify(db_path: str, db_url: str) -> None:
    """Migrate an empty database to head; require an existing one to already be up-to-date.

    To prevent potential data loss, migrations are not applied to a database
    that holds data. Raises DatabaseRevisionError if the database holds tables
    or a revision other than the current head.
    """
    cfg = get_alembic_config(db_url)
    head = ScriptDirectory.from_config(cfg).get_current_head()

    engine = create_engine(db_url)
    try:
        with engine.connect() as connection:
            revision = MigrationContext.configure(connection).get_current_revision()
            is_empty = not inspect(connection).get_table_names()
    finally:
        engine.dispose()

    if revision == head:
        return

    if revision is None and is_empty:
        logger.info("Migrating new database at %s to revision %s", db_path, head)
        command.upgrade(cfg, "head")
        return

    raise DatabaseRevisionError(db_path, revision, head)


def create_db_engine(db_path: str) -> Engine:
    """Create a SQLite engine with FK enforcement and verify its migration revision.

    A new database is migrated to head. An existing one must already be at head,
    or ``DatabaseRevisionError`` is raised; see :func:`_upgrade_or_verify`.

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
            if canonical not in _verified_paths:
                _upgrade_or_verify(db_path, db_url)
                _verified_paths.add(canonical)

    return engine
