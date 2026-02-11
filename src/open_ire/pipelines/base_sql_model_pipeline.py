import logging
from pathlib import Path
from typing import Self

from scrapy import Spider
from scrapy.crawler import Crawler
from sqlalchemy import Engine, event
from sqlmodel import Session, SQLModel, create_engine, select

from open_ire.errors import ConfigurationError
from open_ire.items import ArticleItem
from open_ire.models import Article

logger = logging.getLogger(__name__)


class BaseSQLModelPipeline:
    """Base setup for pipelines that interact with the SQLite database."""

    def __init__(self, db_path: str, files_base_path: str | None = None) -> None:
        self.engine: Engine | None = None
        self.db_path = db_path
        self.files_base_path = files_base_path

    @staticmethod
    def find_existing_article(session: Session, item: ArticleItem) -> Article | None:
        return session.exec(
            select(Article).where(
                Article.repository == item.repository,
                Article.reference == item.reference,
            )
        ).first()

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> Self:
        db_path = crawler.settings.get("OPEN_IRE_DATABASE_FILE")
        if not db_path:
            conf = "OPEN_IRE_DATABASE_FILE"
            raise ConfigurationError(conf)

        parent_dir = Path(db_path).parent
        if not parent_dir.exists():
            parent_dir.mkdir(parents=True, exist_ok=True)
            logger.info("Created OPEN_IRE database directory at %s", parent_dir)

        if not (files_base_path := crawler.settings.get("FILES_STORE", "")):
            conf = "FILES_STORE"
            raise ConfigurationError(conf)

        return cls(db_path, files_base_path)

    def open_spider(self, spider: Spider) -> None:  # noqa: ARG002
        if self.engine:
            return

        self.engine = create_engine(
            f"sqlite:///{self.db_path}", connect_args={"check_same_thread": False}
        )

        # As of v3.6.19, SQLite does not enforce foreign key constraints by default.
        event.listen(
            self.engine,
            "connect",
            lambda dbapi_connection, _: dbapi_connection.execute("PRAGMA foreign_keys=ON"),
        )

        SQLModel.metadata.create_all(self.engine)

    def close_spider(self, spider: Spider) -> None:  # noqa: ARG002
        if self.engine:
            self.engine.dispose()
