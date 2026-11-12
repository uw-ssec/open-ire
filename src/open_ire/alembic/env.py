"""Alembic environment configuration for Open IRE."""

from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

import open_ire.models  # noqa: F401

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
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
