import getpass
import logging
import shlex
import socket
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from scrapy import Spider, signals
from scrapy.crawler import Crawler
from scrapy.exceptions import CloseSpider, NotConfigured
from scrapy.settings import Settings
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from open_ire import __version__
from open_ire.db import create_db_engine
from open_ire.enums import RunKind
from open_ire.extensions.run_recorder import RunRecorder
from open_ire.models import Author, RowChange, Run
from open_ire.provenance import git_commit


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "open_ire.db"


@pytest.fixture
def crawler(db_path: Path) -> Crawler:
    mock_crawler = MagicMock(spec=Crawler)
    mock_crawler.settings = Settings({"OPEN_IRE_DATABASE_FILE": str(db_path)})
    mock_crawler.signals = MagicMock()
    mock_crawler.stats = MagicMock()
    mock_crawler.stats.get_stats.return_value = {
        "item_scraped_count": 3,
        "start_time": datetime(2026, 11, 13, 9, 30, tzinfo=UTC),
    }
    return cast(Crawler, mock_crawler)


@pytest.fixture
def spider() -> Spider:
    mock_spider = MagicMock(spec=Spider)
    mock_spider.name = "test_spider"
    return cast(Spider, mock_spider)


@pytest.fixture
def recorder(crawler: Crawler, spider: Spider) -> Iterator[RunRecorder]:
    """A recorder whose run is open."""
    recorder = RunRecorder.from_crawler(crawler)
    recorder.spider_opened(spider)
    yield recorder
    if recorder.engine is not None:
        recorder.engine.dispose()


@pytest.fixture
def engine(db_path: Path, recorder: RunRecorder) -> Iterator[Engine]:
    """An engine on the recorded database, as a pipeline would create it."""
    engine = create_db_engine(str(db_path))
    yield engine
    engine.dispose()


def _add_author(engine: Engine) -> None:
    with Session(engine) as session:
        session.add(Author(full_name="Jane Doe", canonical_name="doe jane"))
        session.commit()


class TestRunLifecycle:
    def test_from_crawler_connects_signals(self, crawler: Crawler) -> None:
        recorder = RunRecorder.from_crawler(crawler)

        connect = cast(Any, crawler.signals.connect)
        connect.assert_any_call(recorder.spider_opened, signal=signals.spider_opened)
        connect.assert_any_call(recorder.spider_closed, signal=signals.spider_closed)

    @pytest.mark.parametrize("db_file", [None, ":memory:"])
    def test_from_crawler_requires_database_file(
        self, crawler: Crawler, db_file: str | None
    ) -> None:
        crawler.settings.set("OPEN_IRE_DATABASE_FILE", db_file)

        with pytest.raises(NotConfigured):
            RunRecorder.from_crawler(crawler)

    def test_spider_opened_opens_crawl_run(self, recorder: RunRecorder) -> None:
        assert recorder.engine is not None
        with Session(recorder.engine) as session:
            run = session.get_one(Run, recorder.run_id)

        assert run.kind == RunKind.CRAWL
        assert run.spider == "test_spider"
        assert run.command == shlex.join(sys.argv)
        assert run.user == getpass.getuser()
        assert run.hostname == socket.gethostname()
        assert run.version == __version__
        assert run.commit == git_commit()
        assert run.started_at is not None
        assert run.finished_at is None
        assert run.finish_reason is None

    def test_writes_during_the_crawl_belong_to_its_run(
        self, recorder: RunRecorder, engine: Engine
    ) -> None:
        _add_author(engine)

        with Session(engine) as session:
            assert session.exec(select(RowChange.run_id)).all() == [recorder.run_id]

    def test_spider_closed_records_finish(self, recorder: RunRecorder, engine: Engine) -> None:
        recorder.spider_closed("finished")

        with Session(engine) as session:
            run = session.get_one(Run, recorder.run_id)

        assert run.finished_at is not None
        assert run.finish_reason == "finished"
        assert run.stats == {
            "item_scraped_count": 3,
            "start_time": "2026-11-13 09:30:00+00:00",
        }

    def test_writes_after_spider_closed_are_rejected(
        self, recorder: RunRecorder, engine: Engine
    ) -> None:
        recorder.spider_closed("finished")

        with pytest.raises(IntegrityError, match="No run is open"):
            _add_author(engine)


class TestRunCannotOpen:
    def test_another_open_run_closes_spider(
        self,
        crawler: Crawler,
        spider: Spider,
        db_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        create_db_engine(str(db_path)).dispose()
        with closing(sqlite3.connect(db_path)) as connection:
            connection.execute("INSERT INTO run (user, notes) VALUES ('me', 'fix a typo')")
            connection.commit()
        recorder = RunRecorder.from_crawler(crawler)

        with caplog.at_level(logging.ERROR), pytest.raises(CloseSpider) as excinfo:
            recorder.spider_opened(spider)

        assert excinfo.value.reason == "run_recorder_failed"
        assert "(manual, by me, started" in caplog.text
        assert "Traceback" not in caplog.text
        assert recorder.run_id is None
        assert recorder.engine is None, "the engine must be disposed"

        recorder.spider_closed("run_recorder_failed")  # does nothing

    def test_unreachable_database_closes_spider(
        self,
        crawler: Crawler,
        spider: Spider,
        db_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        db_path.write_text("not a database")
        recorder = RunRecorder.from_crawler(crawler)

        with caplog.at_level(logging.ERROR), pytest.raises(CloseSpider):
            recorder.spider_opened(spider)

        assert f"Could not open a run in {db_path}" in caplog.text
        assert "Traceback" in caplog.text
        assert recorder.engine is None
