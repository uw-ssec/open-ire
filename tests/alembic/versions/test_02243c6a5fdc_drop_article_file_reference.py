"""Check removal from a populated database preserves downloaded articles."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
from alembic import command

from open_ire.db import get_alembic_config


def test_drop_references_preserves_article_and_download(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'references.db'}"
    config = get_alembic_config(url)
    command.upgrade(config, "9843ce2b584f")
    engine = sa.create_engine(url)
    metadata = sa.MetaData()
    metadata.reflect(engine)
    article_id = uuid4().hex
    timestamp = datetime.now(UTC)
    records = {
        "article": {
            "id": article_id,
            "reference": "123",
            "repository": "test",
            "title": "Example",
            "url": "https://example.com/article",
            "created_at": timestamp,
            "updated_at": timestamp,
        },
        "article_file": {
            "id": uuid4().hex,
            "article_id": article_id,
            "created_at": timestamp,
            "url": "https://example.com/article.pdf",
            "path": "article.pdf",
            "checksum": "abc123",
        },
        "article_file_reference": {
            "id": uuid4().hex,
            "article_id": article_id,
            "created_at": timestamp,
            "url": "https://example.com/data.csv",
        },
    }
    try:
        with engine.begin() as connection:
            for name, values in records.items():
                connection.execute(metadata.tables[name].insert().values(**values))
            before = {
                name: connection.execute(sa.select(metadata.tables[name])).all()
                for name in ("article", "article_file")
            }

        command.upgrade(config, "02243c6a5fdc")
        assert "article_file_reference" not in sa.inspect(engine).get_table_names()
        with engine.connect() as connection:
            for name, rows in before.items():
                assert connection.execute(sa.select(metadata.tables[name])).all() == rows
        command.check(config)

        command.downgrade(config, "9843ce2b584f")
        with engine.connect() as connection:
            assert (
                connection.execute(sa.select(metadata.tables["article_file_reference"])).all() == []
            )
        command.upgrade(config, "head")
        command.check(config)
    finally:
        engine.dispose()
