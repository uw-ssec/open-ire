"""Record who changed the database, when, and why, as runs and the row changes made in them.

Every write to an audited table must happen while exactly one Run is open.
Database triggers, installed by :func:`refresh_audit_triggers`, reject a write
when no run is open, and otherwise record each inserted, updated, or deleted row
as a RowChange of the open run. Because the triggers live in the database, they
record writes from every client alike: crawls, migrations, notebooks, and
database browsers.

See docs/decisions/0004-record-run-provenance-with-database-triggers.md.
"""

import getpass
import json
import shlex
import socket
import sys
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, Engine, insert, inspect, select, update
from sqlmodel import col

from open_ire import __version__
from open_ire.enums import RunKind
from open_ire.errors import OpenRunError
from open_ire.models import Run

# Tables whose writes are not recorded: the run tables themselves, and
# Alembic's bookkeeping.
UNAUDITED_TABLES = frozenset({"alembic_version", "run", "row_change"})

# Bumped by pipelines whenever a crawl sees a row again, so it alone is not a
# change worth recording.
IGNORED_COLUMNS = frozenset({"updated_at"})

_TRIGGER_PREFIX = "audit_"

# Shown by the database client when a write is rejected, so say how to fix it.
_NO_OPEN_RUN = (
    "No run is open, and every change must belong to one. Open a run first: "
    "INSERT INTO run (user, notes) VALUES ('<your name>', '<why you are making this change>'); "
    "then close it when done: "
    "UPDATE run SET finished_at = CURRENT_TIMESTAMP WHERE finished_at IS NULL"
)
_ANOTHER_RUN_OPEN = (
    "Another run is open, and only one can be open at a time. Find it with: "
    "SELECT * FROM run WHERE finished_at IS NULL"
)


def begin_run(
    connection: Connection,
    kind: RunKind,
    *,
    notes: str | None = None,
    spider: str | None = None,
) -> uuid.UUID:
    """Open a run made by this process, recording its user, host, command, and version.

    The run is visible to other connections once ``connection``'s transaction
    commits.

    Raises:
        OpenRunError: Another run is open.
    """
    if open_run := connection.execute(select(Run).where(col(Run.finished_at).is_(None))).first():
        raise OpenRunError(open_run.id.hex, open_run.kind, open_run.user, open_run.started_at)

    run_id = uuid.uuid4()
    connection.execute(
        insert(Run).values(
            id=run_id,
            kind=kind,
            user=getpass.getuser(),
            notes=notes,
            spider=spider,
            command=shlex.join(sys.argv),
            hostname=socket.gethostname(),
            version=__version__,
            started_at=datetime.now(UTC),
        )
    )
    return run_id


def finish_run(
    connection: Connection,
    run_id: uuid.UUID,
    reason: str,
    stats: dict[str, Any] | None = None,
) -> None:
    """Close a run, recording why it ended and, for crawls, its Scrapy stats."""
    connection.execute(
        update(Run)
        .where(col(Run.id) == run_id)
        .values(
            finished_at=datetime.now(UTC),
            finish_reason=reason,
            # Stats hold datetimes and other values the JSON column can't store.
            stats=None if stats is None else json.loads(json.dumps(stats, default=str)),
        )
    )


@contextmanager
def manual_run(engine: Engine, notes: str) -> Iterator[uuid.UUID]:
    """Open a manual run for the duration of the block, for writes from notebooks and scripts.

    Closes the run with reason "finished", or "failed" if the block raises.

    Raises:
        OpenRunError: Another run is open.
    """
    with engine.begin() as connection:
        run_id = begin_run(connection, RunKind.MANUAL, notes=notes)

    reason = "failed"
    try:
        yield run_id
        reason = "finished"
    finally:
        with engine.begin() as connection:
            finish_run(connection, run_id, reason)


def refresh_audit_triggers(connection: Connection) -> None:
    """Drop the audit triggers and recreate them from the database's current schema.

    Besides the triggers on each audited table, creates triggers on ``run`` that
    keep a second run from opening (an expression index could do this, but
    Alembic can't compare one, and warns on every autogenerate).

    Run after every migration, because a migration can add tables or columns
    the triggers don't cover yet, and SQLite drops a table's triggers whenever
    Alembic rebuilds the table. If the database has no run tables (before the
    migration that adds them), only drops the triggers.
    """
    names: Sequence[str] = (
        connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE ? ESCAPE '\\'",
            (_TRIGGER_PREFIX.replace("_", "\\_") + "%",),
        )
        .scalars()
        .all()
    )
    for name in names:
        connection.exec_driver_sql(f"DROP TRIGGER {_identifier(name)}")

    inspector = inspect(connection)
    tables = inspector.get_table_names()
    if not {"run", "row_change"} <= set(tables):
        return

    for statement in _one_open_run_triggers():
        connection.exec_driver_sql(statement)
    for table in tables:
        if table in UNAUDITED_TABLES or table.startswith(("sqlite_", "_alembic_tmp_")):
            continue
        columns = [column["name"] for column in inspector.get_columns(table)]
        key = inspector.get_pk_constraint(table)["constrained_columns"]
        for statement in _audit_triggers(table, columns, key):
            connection.exec_driver_sql(statement)


def _one_open_run_triggers() -> list[str]:
    """Return triggers that abort opening or reopening a run while another is open.

    SQLite allows one writer at a time, so two processes can't both pass the
    check.
    """
    return [
        f"""
CREATE TRIGGER {_identifier(_TRIGGER_PREFIX + "run_one_open_" + event.split()[0].lower())}
BEFORE {event} ON run
WHEN NEW.finished_at IS NULL
  AND EXISTS (SELECT 1 FROM run WHERE finished_at IS NULL AND id IS NOT NEW.id)
BEGIN
  SELECT RAISE(ABORT, {_literal(_ANOTHER_RUN_OPEN)});
END"""
        for event in ("INSERT", "UPDATE OF finished_at")
    ]


def _audit_triggers(table: str, columns: list[str], key: list[str]) -> list[str]:
    """Return the CREATE TRIGGER statements recording inserts, updates, and deletes on a table.

    Each trigger first aborts the write if no run is open, then inserts a
    RowChange for the open run. An update that changes only ignored columns
    records nothing.
    """
    changed_values = _changes_table(
        {
            c: f"CASE WHEN OLD.{_identifier(c)} IS NOT NEW.{_identifier(c)} "
            f"THEN json_array(OLD.{_identifier(c)}, NEW.{_identifier(c)}) END"
            for c in columns
            if c not in IGNORED_COLUMNS
        }
    )
    deleted_values = _changes_table({c: f"json_array(OLD.{_identifier(c)}, NULL)" for c in columns})
    return [
        _audit_trigger(table, "INSERT", _row_id("NEW", key), "'{}'", "run"),
        _audit_trigger(
            table,
            "UPDATE",
            _row_id("NEW", key),
            "c.changes",
            f"run, {changed_values} AS c",
            "AND c.changes <> '{}'",
        ),
        _audit_trigger(
            table, "DELETE", _row_id("OLD", key), "c.changes", f"run, {deleted_values} AS c"
        ),
    ]


def _audit_trigger(
    table: str,
    operation: str,
    row_id: str,
    changes: str,
    source: str,
    condition: str = "",
) -> str:
    """Return one CREATE TRIGGER statement that records ``operation`` on ``table``.

    ``row_id`` and ``changes`` are SQL expressions over the OLD/NEW rows;
    ``source`` is the FROM clause they are evaluated in, which must include
    ``run`` so the open run's id can be read; ``condition`` is appended to the
    WHERE clause, to skip recording.
    """
    name = _identifier(f"{_TRIGGER_PREFIX}{table}_{operation.lower()}")
    return f"""
CREATE TRIGGER {name} AFTER {operation} ON {_identifier(table)}
BEGIN
  SELECT RAISE(ABORT, {_literal(_NO_OPEN_RUN)})
  WHERE NOT EXISTS (SELECT 1 FROM run WHERE finished_at IS NULL);
  INSERT INTO row_change (run_id, table_name, row_id, operation, changes, changed_at)
  SELECT run.id, {_literal(table)}, {row_id}, '{operation}', {changes},
         strftime('%Y-%m-%d %H:%M:%f000', 'now')
  FROM {source}
  WHERE run.finished_at IS NULL {condition};
END"""


def _changes_table(values: dict[str, str]) -> str:
    """Return a one-row table whose ``changes`` column is a JSON object of the non-null values.

    ``values`` maps each column name to a SQL expression giving its JSON
    value, or NULL to leave the column out.
    """
    pairs = ", ".join(f"{_literal(name)}, {value}" for name, value in values.items())
    return (
        "(SELECT json_group_object(key, json(value)) AS changes "
        f"FROM json_each(json_object({pairs})) WHERE type <> 'null')"
    )


def _row_id(alias: str, key: list[str]) -> str:
    """Return the SQL for a row's primary key, comma-joining composite keys."""
    if not key:
        return f"{alias}.rowid"
    return " || ',' || ".join(f"{alias}.{_identifier(column)}" for column in key)


def _identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
