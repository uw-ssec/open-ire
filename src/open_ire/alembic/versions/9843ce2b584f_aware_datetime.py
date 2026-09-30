"""Use timezone-aware datetimes (default for SQLModel 0.0.45)

Revision ID: 9843ce2b584f
Revises: 473c9757ac3f
Create Date: 2026-09-21 21:01:08.459535

Existing timestamps are America/Los_Angeles wall times. Run this revision
before writing UTC timestamps; SQLite cannot distinguish the two conventions.
SQLite keeps its DATETIME columns and stores UTC without an offset, which
SQLModel 0.0.45 reads as aware UTC.

Ambiguous or nonexistent local times at DST transitions are converted with
the offset in effect before the transition (fold=0) and logged as warnings.
Downgrading loses timezone information again.
"""

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "9843ce2b584f"
down_revision: str | None = "473c9757ac3f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger(__name__)

_SOURCE_TIMEZONE = "America/Los_Angeles"
_TIMESTAMP_COLUMNS = {
    "article": ("created_at", "updated_at"),
    "article_file": ("created_at",),
    "article_file_reference": ("created_at",),
    "article_oa_evidence": ("created_at",),
    "article_deposit_status_transition": ("changed_at",),
    "author": ("created_at", "updated_at"),
    "author_identifier": ("created_at",),
    "authorship": ("created_at", "updated_at"),
}


def _convert_datetime(value: datetime | None, *, to_utc: bool, where: str) -> datetime | None:
    if value is None:
        return None

    local_zone = ZoneInfo(_SOURCE_TIMEZONE)
    if to_utc:
        if value.tzinfo is None:
            first = value.replace(tzinfo=local_zone, fold=0)
            if first.utcoffset() != value.replace(tzinfo=local_zone, fold=1).utcoffset():
                logger.warning(
                    "Ambiguous (fall-back) or nonexistent (spring-forward) local timestamp %s in "
                    "%s timezone at %s; assuming %s.",
                    value,
                    _SOURCE_TIMEZONE,
                    where,
                    first.tzname(),
                )
            value = first
        return value.astimezone(UTC).replace(tzinfo=None)

    return value.replace(tzinfo=UTC).astimezone(local_zone).replace(tzinfo=None)


def _convert_sqlite(*, to_utc: bool) -> None:
    if context.is_offline_mode():
        msg = "The SQLite datetime data migration requires an online connection."
        raise RuntimeError(msg)

    connection = op.get_bind()
    # Use historical SQLAlchemy types, not current SQLModel models, so reads
    # retain the naive values that need conversion. A savepoint rolls back all
    # tables if any update fails.
    with connection.begin_nested():
        for table_name, column_names in _TIMESTAMP_COLUMNS.items():
            table = sa.Table(table_name, sa.MetaData(), autoload_with=connection)
            primary_keys = list(table.primary_key.columns)
            columns = [table.c[name] for name in column_names]
            statement = (
                table.update()
                .where(
                    sa.and_(
                        *(column == sa.bindparam(f"pk_{column.name}") for column in primary_keys)
                    )
                )
                .values({name: sa.bindparam(f"new_{name}") for name in column_names})
            )
            with connection.execute(sa.select(*primary_keys, *columns)) as result:
                for rows in result.mappings().partitions(500):
                    updates = []
                    for row in rows:
                        values = {f"pk_{column.name}": row[column.name] for column in primary_keys}
                        pk = ", ".join(
                            f"{column.name}={row[column.name]}" for column in primary_keys
                        )
                        values.update(
                            {
                                f"new_{name}": _convert_datetime(
                                    row[name], to_utc=to_utc, where=f"{table_name}({pk}).{name}"
                                )
                                for name in column_names
                            }
                        )
                        updates.append(values)
                    connection.execute(statement, updates)


def upgrade() -> None:
    _convert_sqlite(to_utc=True)


def downgrade() -> None:
    _convert_sqlite(to_utc=False)
