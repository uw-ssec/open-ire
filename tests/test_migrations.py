"""Tests that the Alembic migration chain is reversible and matches the models.

These guard two blind spots that plain schema tests miss: broken ``downgrade()``
functions (rarely exercised) and drift between the migration chain and the
``table=True`` models. Both run against a throwaway SQLite database in
``tmp_path`` using the same :func:`get_alembic_config` the app uses in
production, so no external services are needed.
"""

from pathlib import Path

from alembic import command
from sqlalchemy import create_engine, inspect

from open_ire.db import get_alembic_config
from tests.test_models import table_models

# Alembic's own bookkeeping table; not declared by any SQLModel model.
ALEMBIC_BOOKKEEPING = {"alembic_version"}


def _config_for(db_path: Path):
    """Alembic config targeting a fresh SQLite file at ``db_path``."""
    return get_alembic_config(f"sqlite:///{db_path}")


def test_migrations_round_trip(tmp_path: Path) -> None:
    """``upgrade head`` -> ``downgrade base`` -> ``upgrade head`` must all succeed.

    Revision-agnostic, so it automatically covers future migrations. A broken
    ``downgrade()`` raises during ``downgrade(base)`` and fails the test.
    """
    cfg = _config_for(tmp_path / "roundtrip.db")

    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


def test_migrated_schema_matches_models(tmp_path: Path) -> None:
    """After ``upgrade head``, present tables equal the ``table=True`` models.

    Strict set equality (excluding ``alembic_version``) catches both a table the
    models declare but no migration creates, and an orphan table a migration
    creates but no model declares.
    """
    db_path = tmp_path / "schema.db"
    command.upgrade(_config_for(db_path), "head")

    engine = create_engine(f"sqlite:///{db_path}")
    try:
        present = set(inspect(engine).get_table_names()) - ALEMBIC_BOOKKEEPING
    finally:
        engine.dispose()

    expected = {model.__tablename__ for model in table_models()}
    assert present == expected
