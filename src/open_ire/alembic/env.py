"""Alembic environment configuration for Open IRE."""

import uuid
from logging.config import fileConfig
from pathlib import Path

from alembic import context, util
from sqlalchemy import Connection, engine_from_config, inspect, pool
from sqlmodel import SQLModel

import open_ire.models  # noqa: F401
from open_ire.enums import RunKind
from open_ire.provenance import begin_run, finish_run, refresh_audit_triggers

# Apply alembic.ini's logging config, which is what makes `alembic` CLI output visible. Only the
# CLI reaches this: db.py builds a fileless Config(), so config_file_name is None during a crawl
# and Scrapy's logging setup is left alone.
if context.config.config_file_name is not None:
    fileConfig(context.config.config_file_name)

target_metadata = SQLModel.metadata


def get_url() -> str:
    """Resolve the database URL.

    Priority:
    1. URL passed programmatically via config attributes (production runtime).
    2. OPEN_IRE_DATABASE_FILE from base settings, which reads the environment
       variable of the same name.

    Raises:
        FileNotFoundError: The OPEN_IRE_DATABASE_FILE fallback names a file that
            does not exist. SQLite would otherwise silently create an empty
            database there, hiding a mistyped path. Only the CLI reaches this
            branch; crawls pass the URL programmatically and create new
            databases themselves.
    """
    url: str | None = context.config.attributes.get("sqlalchemy.url")
    if url:
        return url

    from open_ire.settings.base import OPEN_IRE_DATABASE_FILE  # noqa: PLC0415

    if not Path(OPEN_IRE_DATABASE_FILE).exists():
        msg = f"Database file '{OPEN_IRE_DATABASE_FILE}' does not exist; check OPEN_IRE_DATABASE_FILE."
        raise FileNotFoundError(msg)

    return f"sqlite:///{OPEN_IRE_DATABASE_FILE}"


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (generates SQL without a live connection)."""
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (with a live database connection)."""
    configuration = context.config.get_section(context.config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            _run_recorded_migrations(connection)


def _run_recorded_migrations(connection: Connection) -> None:
    """Run the migrations as a migration run, and refresh the audit triggers after them.

    Commands that change the revision (upgrade, downgrade, stamp) open a Run, so
    that the audit triggers accept and record the migrations' writes. Its notes
    name the revisions, followed by any ``-x notes="..."`` given on the command
    line. A database without the run tables (new, or older than the migration
    that adds them) gets no run.

    Commands that only read the revision (``alembic check``, autogenerate) write
    nothing.

    Raises:
        OpenRunError: Another run is open.
    """
    migration_context = context.get_context()
    if "destination_rev" not in migration_context.opts:
        context.run_migrations()
        return

    current = migration_context.get_current_heads()
    target = util.to_tuple(context.get_revision_argument(), default=())
    if set(current) == set(target):
        return

    run_id = None
    if _has_run_tables(connection):
        notes = f"Migrate from {', '.join(current) or 'base'} to {', '.join(target) or 'base'}"
        if extra := context.get_x_argument(as_dictionary=True).get("notes"):
            notes = f"{notes}: {extra}"
        run_id = begin_run(connection, RunKind.MIGRATION, notes=notes)
        connection.commit()

    try:
        context.run_migrations()
    except Exception:
        connection.rollback()  # a failed migration may leave its transaction open
        _finish_migration_run(connection, run_id, "failed")
        raise
    _finish_migration_run(connection, run_id, "finished")


def _finish_migration_run(connection: Connection, run_id: uuid.UUID | None, reason: str) -> None:
    """Refresh the audit triggers, close the migration run, if any, and commit.

    Runs after a failed migration too: Alembic commits each SQLite migration
    as it goes, so the ones before the failure stand, and a table one of them
    rebuilt has lost its triggers. Alembic also commits nothing after the last
    migration itself, so work done here would be rolled back when the
    connection closes. A downgrade past the migration that adds the run tables
    leaves no run to close.
    """
    refresh_audit_triggers(connection)
    if run_id is not None and _has_run_tables(connection):
        finish_run(connection, run_id, reason)
    connection.commit()


def _has_run_tables(connection: Connection) -> bool:
    return inspect(connection).has_table("run")


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
