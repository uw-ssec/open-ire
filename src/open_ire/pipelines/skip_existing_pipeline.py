import logging
from typing import Any

from scrapy.crawler import Crawler
from scrapy.exceptions import DropItem
from sqlmodel import Session, select

from open_ire.items import ArticleItem
from open_ire.models import Article, ArticleFile
from open_ire.pipelines.base_sql_model_pipeline import BaseSQLModelPipeline

logger = logging.getLogger(__name__)


class SkipExistingPipeline(BaseSQLModelPipeline):
    """
    Skips items that already exist in the database.

    Two independent levels of skipping are supported, both off by default:

    - ``OPEN_IRE_SKIP_EXISTING``: skip every article already in the database.
    - ``OPEN_IRE_SKIP_EXISTING_WITH_FILES``: skip existing articles that
      already have a stored file, but re-process those whose file download
      previously failed so it can be re-attempted.

    ``OPEN_IRE_SKIP_EXISTING`` takes precedence: when it is enabled, every
    existing article is skipped regardless of the other setting.
    """

    @staticmethod
    def _skip_existing(crawler: Crawler) -> bool:
        return crawler.settings.getbool("OPEN_IRE_SKIP_EXISTING", False)

    @staticmethod
    def _skip_existing_with_files(crawler: Crawler) -> bool:
        return crawler.settings.getbool("OPEN_IRE_SKIP_EXISTING_WITH_FILES", False)

    @classmethod
    def _skipping_enabled(cls, crawler: Crawler) -> bool:
        return cls._skip_existing(crawler) or cls._skip_existing_with_files(crawler)

    @staticmethod
    def _has_stored_files(session: Session, article: Article) -> bool:
        statement = select(ArticleFile).where(ArticleFile.article_id == article.id)
        return session.exec(statement).first() is not None

    def _skip_reason(self, session: Session, article: Article) -> str | None:
        """Return why *article* should be skipped, or ``None`` to keep it."""
        assert self.crawler is not None
        if self._skip_existing(self.crawler):
            return "already exists"
        if self._skip_existing_with_files(self.crawler) and self._has_stored_files(
            session, article
        ):
            return "already exists with a stored file"
        return None

    def open_spider(self) -> None:
        if self.crawler is None or not self._skipping_enabled(self.crawler):
            return

        super().open_spider()

    def process_item(self, item: Any) -> Any:
        if not isinstance(item, ArticleItem):
            return item

        if self.crawler is None or not self._skipping_enabled(self.crawler):
            return item

        with Session(self.engine) as session:
            existing_article = self.find_existing_article(session, item)
            if existing_article is None:
                return item

            reason = self._skip_reason(session, existing_article)
            if reason is None:
                return item

        active_logger = self.crawler.spider.logger if self.crawler.spider else logger
        active_logger.info(
            "Skipping article '%s' from repository '%s': %s.",
            item.reference,
            item.repository,
            reason,
        )
        msg = f"Article {reason} in database."
        raise DropItem(msg)
