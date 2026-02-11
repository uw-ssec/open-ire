import logging

from scrapy.crawler import Crawler
from scrapy.exceptions import DropItem
from sqlmodel import Session

from open_ire.items import ArticleItem
from open_ire.pipelines.base_sql_model_pipeline import BaseSQLModelPipeline

logger = logging.getLogger(__name__)


class SkipExistingPipeline(BaseSQLModelPipeline):
    """
    Skips items that already exist in the database.

    Intended to short-circuit file downloads and downstream pipelines when OPEN_IRE_SKIP_EXISTING is enabled.
    """

    @staticmethod
    def _should_skip_existing(crawler: Crawler) -> bool:
        return bool(crawler.settings.getbool("OPEN_IRE_SKIP_EXISTING", False))

    def open_spider(self) -> None:
        if self.crawler is None or not self._should_skip_existing(self.crawler):
            return

        super().open_spider()

    def process_item(self, item: ArticleItem) -> ArticleItem:
        if self.crawler is None or not self._should_skip_existing(self.crawler):
            return item

        with Session(self.engine) as session:
            if self.find_existing_article(session, item) is not None:
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
