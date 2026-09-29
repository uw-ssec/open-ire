"""Tests for database initialization and migration verification."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from alembic.script import ScriptDirectory

import open_ire.db as db_module
from open_ire.db import create_db_engine, get_alembic_config
from open_ire.errors import DatabaseRevisionError


@pytest.fixture(autouse=True)
def _clear_verified_paths():
    """Keep the module-level cache of verified paths from leaking between tests."""
    db_module._verified_paths.clear()
    yield
    db_module._verified_paths.clear()


@pytest.fixture
def head() -> str:
    cfg = get_alembic_config("sqlite:///:memory:")
    revision = ScriptDirectory.from_config(cfg).get_current_head()
    assert revision is not None
    return revision


def _revision(db_path: Path) -> str | None:
    with closing(sqlite3.connect(db_path)) as connection:
        row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    return None if row is None else str(row[0])


def _execute(db_path: Path, statement: str) -> None:
    with closing(sqlite3.connect(db_path)) as connection:
        connection.execute(statement)
        connection.commit()


def test_new_database_is_migrated_to_head(tmp_path: Path, head: str) -> None:
    db_path = tmp_path / "new.db"

    create_db_engine(str(db_path)).dispose()

    assert _revision(db_path) == head


def test_empty_file_is_migrated_to_head(tmp_path: Path, head: str) -> None:
    db_path = tmp_path / "empty.db"
    db_path.touch()

    create_db_engine(str(db_path)).dispose()

    assert _revision(db_path) == head


def test_database_at_head_is_accepted(tmp_path: Path, head: str) -> None:
    db_path = tmp_path / "current.db"
    create_db_engine(str(db_path)).dispose()
    db_module._verified_paths.clear()

    create_db_engine(str(db_path)).dispose()

    assert _revision(db_path) == head


def test_stale_revision_is_rejected(tmp_path: Path, head: str) -> None:
    db_path = tmp_path / "stale.db"
    create_db_engine(str(db_path)).dispose()
    _execute(db_path, "UPDATE alembic_version SET version_num = '1234567890ab'")
    db_module._verified_paths.clear()

    with pytest.raises(DatabaseRevisionError) as excinfo:
        create_db_engine(str(db_path))

    assert excinfo.value.revision == "1234567890ab"
    assert excinfo.value.head == head
    assert "Database Migrations" in str(excinfo.value)
    assert _revision(db_path) == "1234567890ab", "the database must be left untouched"


def test_unversioned_database_with_tables_is_rejected(tmp_path: Path) -> None:
    db_path = tmp_path / "unversioned.db"
    _execute(db_path, "CREATE TABLE article (id TEXT PRIMARY KEY)")

    with pytest.raises(DatabaseRevisionError) as excinfo:
        create_db_engine(str(db_path))

    assert excinfo.value.revision is None
    assert "unversioned" in str(excinfo.value)
