"""Check the run tables migration, and how env.py records migrations as runs."""

import argparse
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.util import CommandError

from open_ire.db import get_alembic_config
from open_ire.errors import OpenRunError

PREVIOUS_REVISION = "02243c6a5fdc"
REVISION = "518042dc42e0"

INSERT_AUTHOR = (
    "INSERT INTO author (full_name, canonical_name, explicitly_searched, created_at, updated_at) "
    "VALUES ('Jane Doe', 'doe jane', 0, '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "open_ire.db"


@pytest.fixture
def config(db_path: Path) -> Config:
    return get_alembic_config(f"sqlite:///{db_path}")


@pytest.fixture
def at_head(config: Config) -> Iterator[None]:
    command.upgrade(config, "head")
    yield
    command.check(config)


def test_upgrade_installs_triggers_that_require_a_run(db_path: Path, config: Config) -> None:
    command.upgrade(config, PREVIOUS_REVISION)
    _execute(db_path, INSERT_AUTHOR)

    command.upgrade(config, REVISION)

    assert "audit_author_insert" in _triggers(db_path)
    assert _query(db_path, "SELECT count(*) FROM author") == [(1,)]
    assert _query(db_path, "SELECT count(*) FROM run") == [(0,)], "nothing to record a run in"
    with pytest.raises(sqlite3.IntegrityError, match="No run is open"):
        _execute(db_path, INSERT_AUTHOR)


@pytest.mark.usefixtures("at_head")
def test_downgrade_removes_triggers_and_run_tables(db_path: Path, config: Config) -> None:
    command.downgrade(config, PREVIOUS_REVISION)

    assert _triggers(db_path) == []
    tables = {name for (name,) in _query(db_path, "SELECT name FROM sqlite_master")}
    assert not {"run", "row_change"} & tables
    _execute(db_path, INSERT_AUTHOR)

    command.upgrade(config, "head")


@pytest.mark.usefixtures("at_head")
def test_migrating_commands_record_a_migration_run(db_path: Path, config: Config) -> None:
    config.cmd_opts = argparse.Namespace(x=["notes=testing env.py"])

    command.stamp(config, PREVIOUS_REVISION)
    config.cmd_opts = None
    command.stamp(config, "head")

    assert _query(db_path, "SELECT kind, notes, finish_reason FROM run ORDER BY started_at") == [
        (
            "MIGRATION",
            f"Migrate from {REVISION} to {PREVIOUS_REVISION}: testing env.py",
            "finished",
        ),
        ("MIGRATION", f"Migrate from {PREVIOUS_REVISION} to head", "finished"),
    ]
    assert "audit_author_insert" in _triggers(db_path)


@pytest.mark.usefixtures("at_head")
def test_commands_that_change_nothing_record_nothing(db_path: Path, config: Config) -> None:
    command.upgrade(config, "head")
    command.check(config)

    assert _query(db_path, "SELECT count(*) FROM run") == [(0,)]


@pytest.mark.usefixtures("at_head")
def test_migrating_is_refused_while_a_run_is_open(db_path: Path, config: Config) -> None:
    _execute(db_path, "INSERT INTO run (user, notes) VALUES ('me', 'fix a typo')")

    with pytest.raises(OpenRunError, match="manual, by me"):
        command.stamp(config, PREVIOUS_REVISION)

    assert _query(db_path, "SELECT version_num FROM alembic_version") == [(REVISION,)]
    _execute(db_path, "UPDATE run SET finished_at = CURRENT_TIMESTAMP")


@pytest.mark.usefixtures("at_head")
def test_failed_migration_closes_its_run_and_restores_triggers(
    db_path: Path, config: Config
) -> None:
    """A migration before the failing one may have rebuilt a table, dropping its triggers."""
    _execute(db_path, "DROP TRIGGER audit_author_insert")

    with pytest.raises(CommandError, match="Can't locate revision"):
        command.stamp(config, "deadbeefcafe")

    assert _query(db_path, "SELECT kind, finish_reason FROM run") == [("MIGRATION", "failed")]
    assert "audit_author_insert" in _triggers(db_path)


def _execute(db_path: Path, statement: str) -> None:
    with closing(sqlite3.connect(db_path)) as connection:
        connection.execute(statement)
        connection.commit()


def _query(db_path: Path, statement: str) -> list[tuple[object, ...]]:
    with closing(sqlite3.connect(db_path)) as connection:
        return connection.execute(statement).fetchall()


def _triggers(db_path: Path) -> list[str]:
    rows = _query(db_path, "SELECT name FROM sqlite_master WHERE type = 'trigger'")
    return [str(name) for (name,) in rows]
