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

    Intended to short-circuit file downloads and downstream pipelines when OPEN_IRE_SKIP_EXISTING is enabled.

    When OPEN_IRE_SKIP_EXISTING_REQUIRES_FILES is also enabled (opt-in, per
    spider), existing articles without any stored file are NOT skipped, so
    previously failed file downloads get re-attempted on each run.
    """

    @staticmethod
    def _should_skip_existing(crawler: Crawler) -> bool:
        return bool(crawler.settings.getbool("OPEN_IRE_SKIP_EXISTING", False))

    @staticmethod
    def _skip_requires_files(crawler: Crawler) -> bool:
        return crawler.settings.getbool("OPEN_IRE_SKIP_EXISTING_REQUIRES_FILES", False)

    @staticmethod
    def _has_stored_files(session: Session, article: Article) -> bool:
        statement = select(ArticleFile).where(ArticleFile.article_id == article.id)
        return session.exec(statement).first() is not None

    def open_spider(self) -> None:
        if self.crawler is None or not self._should_skip_existing(self.crawler):
            return

        super().open_spider()

    def process_item(self, item: Any) -> Any:
        if not isinstance(item, ArticleItem):
            return item

        if self.crawler is None or not self._should_skip_existing(self.crawler):
            return item

        with Session(self.engine) as session:
            existing_article = self.find_existing_article(session, item)
            if (
                existing_article is not None
                and self._skip_requires_files(self.crawler)
                and not self._has_stored_files(session, existing_article)
            ):
                # Article is known but its file download previously failed;
                # let it through so the file is re-attempted.
                existing_article = None

            if existing_article is not None:
                if self.crawler.spider:
                    self.crawler.spider.logger.info(
                        "Skipping existing article '%s' from repository '%s'.",
                        item.reference,
                        item.repository,
                    )
                else:
                    logger.info(
                        "Skipping existing article '%s' from repository '%s'.",
                        item.reference,
                        item.repository,
                    )
                msg = "Article already exists in database."
                raise DropItem(msg)

        return item
