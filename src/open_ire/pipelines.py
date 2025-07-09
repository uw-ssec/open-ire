from typing import Self

from pydantic import ValidationError
from scrapy import Spider
from scrapy.crawler import Crawler
from scrapy.exceptions import DropItem
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, create_engine

from open_ire.items import ArticleItem
from open_ire.models import Article, ArticleFile

# Remember to add your pipelines to the `settings.ITEM_PIPELINES` list


class DuplicatesPipeline:
    def __init__(self) -> None:
        self.seen: set[str] = set()

    def process_item(self, item: ArticleItem, spider: Spider) -> ArticleItem:
        if item.reference in self.seen:
            msg = f"Item ID already seen: {item.reference} by {spider.name} spider"
            raise DropItem(msg)
        self.seen.add(item.reference)
        return item


class SQLModelPipeline:
    """
    Persist ArticleItem metadata + downloaded-file info into SQLite via SQLModel.
    """

    def __init__(self, db_path: str) -> None:
        self.db_url = f"sqlite:///{db_path}"
        self.engine = create_engine(self.db_url, connect_args={"check_same_thread": False})

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> Self:
        db_path = crawler.settings.get("OPEN_IRE_DATABASE_FILE")
        if not db_path:
            msg = "OPEN_IRE_DATABASE_FILE must be set in settings.py"
            raise RuntimeError(msg)
        return cls(db_path)

    def open_spider(self, spider: Spider) -> None:  # noqa: ARG002
        SQLModel.metadata.create_all(self.engine)

    def close_spider(self, spider: Spider) -> None:  # noqa: ARG002
        self.engine.dispose()

    def process_item(self, item: ArticleItem, spider: Spider) -> ArticleItem:
        if not item.files:
            msg = f"No files found for article '{item.reference}'."
            raise DropItem(msg)

        valid_files = []
        for file_data in item.files:
            try:
                file_row = ArticleFile(**file_data)
                valid_files.append(file_row)
            except ValidationError:
                spider.logger.warning("Skipping file due to validation error for article.")

        if not valid_files:
            msg = f"All files for article '{item.reference}' failed validation."
            raise DropItem(msg)

        article_row = Article(**item.model_dump(exclude={"files", "file_urls"}))
        with Session(self.engine) as session:
            try:
                session.add(article_row)
                session.commit()
                session.refresh(article_row)

                for file_row in valid_files:
                    file_row.article_id = article_row.id
                    session.add(file_row)

                session.commit()

            except IntegrityError as e:
                session.rollback()
                msg = "Duplicate item found in database."
                raise DropItem(msg) from e

        return item
