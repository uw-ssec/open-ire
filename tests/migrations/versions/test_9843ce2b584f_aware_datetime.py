"""Exercise timezone conversion against the actual pre-upgrade SQLite schema."""

# Legacy database values are deliberately naive.

import logging
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command

from open_ire.db import get_alembic_config

PREVIOUS_REVISION = "473c9757ac3f"
REVISION = "9843ce2b584f"
# Alembic names the migration module, and so its logger, after the file.
MIGRATION_LOGGER = "9843ce2b584f_aware_datetime_py"

# Pacific wall times paired with the same instants in UTC.
LOCAL_TO_UTC = {
    datetime(2026, 1, 15, 12, 30, 45, 123456): datetime(2026, 1, 15, 20, 30, 45, 123456),  # PST
    datetime(2026, 7, 15, 12, 30, 45, 654321): datetime(2026, 7, 15, 19, 30, 45, 654321),  # PDT
}


@pytest.fixture
def legacy_database(tmp_path):
    url = f"sqlite:///{tmp_path / 'legacy.db'}"
    config = get_alembic_config(url)
    command.upgrade(config, PREVIOUS_REVISION)
    engine = sa.create_engine(url)
    metadata = sa.MetaData()
    metadata.reflect(engine)
    with engine.begin() as connection:
        for index, timestamp in enumerate(LOCAL_TO_UTC, start=1):
            article_id = uuid4().hex
            records = {
                "article": {
                    "id": article_id,
                    "reference": str(index),
                    "repository": "test",
                    "title": "Test article",
                    "url": f"https://example.com/{index}",
                    "created_at": timestamp,
                    "updated_at": timestamp,
                },
                "author": {
                    "id": index,
                    "full_name": "Test Author",
                    "canonical_name": "Author, Test",
                    "explicitly_searched": False,
                    "created_at": timestamp,
                    "updated_at": timestamp,
                },
                "article_file": {
                    "id": uuid4().hex,
                    "article_id": article_id,
                    "url": f"https://example.com/{index}.pdf",
                    "checksum": "test",
                    "path": "test.pdf",
                    "created_at": timestamp,
                },
                "article_file_reference": {
                    "id": uuid4().hex,
                    "article_id": article_id,
                    "url": f"https://example.com/reference/{index}",
                    "created_at": timestamp,
                },
                "article_oa_evidence": {
                    "id": uuid4().hex,
                    "article_id": article_id,
                    "kind": "LICENSE",
                    "supports_oa": True,
                    "created_at": timestamp,
                },
                "article_deposit_status_transition": {
                    "id": uuid4().hex,
                    "article_id": article_id,
                    "to_status": "READY",
                    "changed_at": timestamp,
                },
                "author_identifier": {
                    "id": index,
                    "author_id": index,
                    "authority": "test",
                    "identifier": str(index),
                    "created_at": timestamp,
                },
                "authorship": {
                    "article_id": article_id,
                    "author_id": index,
                    "created_at": timestamp,
                    "updated_at": timestamp,
                },
            }
            for name, values in records.items():
                connection.execute(metadata.tables[name].insert().values(**values))
    yield config, engine, metadata
    engine.dispose()


def snapshot(engine, metadata):
    with engine.connect() as connection:
        return {
            table.name: connection.execute(sa.select(table).order_by(*table.primary_key.columns))
            .mappings()
            .all()
            for table in metadata.sorted_tables
        }


def test_upgrade_converts_local_timestamps_to_utc(legacy_database):
    config, engine, metadata = legacy_database
    before = snapshot(engine, metadata)
    command.upgrade(config, REVISION)
    after = snapshot(engine, metadata)
    timestamp_count = 0
    for table_name, rows in before.items():
        if table_name == "alembic_version":
            continue
        for original, converted in zip(rows, after[table_name], strict=True):
            for name, value in original.items():
                if isinstance(value, datetime):
                    assert converted[name] == LOCAL_TO_UTC[value]
                    timestamp_count += 1
                else:
                    assert converted[name] == value
    # 11 timestamp columns across 8 tables, 2 rows each.
    assert timestamp_count == 22


def test_downgrade_restores_original_values(legacy_database):
    config, engine, metadata = legacy_database
    before = snapshot(engine, metadata)
    command.upgrade(config, REVISION)
    command.downgrade(config, PREVIOUS_REVISION)
    assert snapshot(engine, metadata) == before


@pytest.mark.parametrize(
    ("timestamp", "expected"),
    [
        # Nonexistent (spring forward): fold=0 uses PST, the offset before the gap.
        (datetime(2026, 3, 8, 2, 30), datetime(2026, 3, 8, 10, 30)),
        # Ambiguous (fall back): fold=0 picks the first occurrence, in PDT.
        (datetime(2026, 11, 1, 1, 30), datetime(2026, 11, 1, 8, 30)),
    ],
)
def test_dst_transition_timestamp_uses_first_fold_and_warns(
    legacy_database, caplog, timestamp, expected
):
    config, engine, metadata = legacy_database
    table = metadata.tables["authorship"]
    with engine.begin() as connection:
        connection.execute(table.update().values(updated_at=timestamp))
    with caplog.at_level(logging.WARNING, logger=MIGRATION_LOGGER):
        command.upgrade(config, REVISION)
    with engine.connect() as connection:
        assert list(connection.execute(sa.select(table.c.updated_at)).scalars()) == [expected] * 2
    warnings = [r.getMessage() for r in caplog.records if r.name == MIGRATION_LOGGER]
    assert len(warnings) == 2
    assert all("authorship(" in message and ").updated_at" in message for message in warnings)


def test_new_utc_values_downgrade_to_local_time(legacy_database):
    config, engine, metadata = legacy_database
    command.upgrade(config, REVISION)
    with engine.begin() as connection:
        connection.execute(
            metadata.tables["author"]
            .update()
            .values(updated_at=datetime(2026, 7, 15, 22, tzinfo=UTC))
        )
    command.downgrade(config, PREVIOUS_REVISION)
    with engine.connect() as connection:
        timestamps = connection.execute(sa.select(metadata.tables["author"].c.updated_at)).scalars()
        assert list(timestamps) == [datetime(2026, 7, 15, 15)] * 2


def test_fresh_database_can_upgrade(tmp_path):
    config = get_alembic_config(f"sqlite:///{tmp_path / 'fresh.db'}")
    command.upgrade(config, REVISION)
