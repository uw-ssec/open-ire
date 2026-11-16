"""Open a Run for each crawl, so the database's audit triggers accept and record its writes.

See docs/decisions/0004-record-run-provenance-with-database-triggers.md.
"""

import logging
import uuid
from typing import Self

from scrapy import Spider, signals
from scrapy.crawler import Crawler
from scrapy.exceptions import CloseSpider, NotConfigured
from sqlalchemy import Engine

from open_ire.db import create_db_engine
from open_ire.enums import RunKind
from open_ire.errors import OpenRunError
from open_ire.provenance import begin_run, finish_run

logger = logging.getLogger(__name__)


class RunRecorder:
    """Open a crawl run when the spider opens, and close it when the spider closes.

    While the run is open, the database's audit triggers record every write as
    a RowChange of it. If the run can't be opened (another run is open, or the
    database can't be reached), closes the spider with reason
    ``run_recorder_failed``: the triggers would reject its writes anyway. On
    ``spider_closed``, records the finish time, close reason, and stats. A
    process that dies before the spider closes leaves the run open, and the
    next run refuses to start until someone closes it.

    Enable it ahead of ``SharePointBackup`` so its close handler runs first and
    the backed-up database includes the finished run.
    """

    def __init__(self, db_path: str, crawler: Crawler) -> None:
        self.db_path = db_path
        self.crawler = crawler
        self.engine: Engine | None = None
        self.run_id: uuid.UUID | None = None

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> Self:
        db_path = crawler.settings.get("OPEN_IRE_DATABASE_FILE")
        if not db_path or db_path == ":memory:":
            raise NotConfigured

        recorder = cls(db_path, crawler)
        crawler.signals.connect(recorder.spider_opened, signal=signals.spider_opened)
        crawler.signals.connect(recorder.spider_closed, signal=signals.spider_closed)
        return recorder

    def spider_opened(self, spider: Spider) -> None:
        """Open the crawl run.

        Raises:
            CloseSpider: If the run can't be opened. Scrapy doesn't log this
                exception, so the cause is logged here.
        """
        try:
            self.engine = create_db_engine(self.db_path)
            with self.engine.begin() as connection:
                self.run_id = begin_run(connection, RunKind.CRAWL, spider=spider.name)
        except Exception as exc:
            if isinstance(exc, OpenRunError):
                logger.error("Could not open a run in %s: %s", self.db_path, exc)
            else:
                logger.exception("Could not open a run in %s", self.db_path)
            if self.engine is not None:
                self.engine.dispose()
                self.engine = None
            raise CloseSpider(reason="run_recorder_failed") from exc

        logger.info("Recording run %s in %s", self.run_id, self.db_path)

    def spider_closed(self, reason: str) -> None:
        if self.engine is None or self.run_id is None:
            return

        with self.engine.begin() as connection:
            finish_run(connection, self.run_id, reason, self.crawler.stats.get_stats())

        self.engine.dispose()
