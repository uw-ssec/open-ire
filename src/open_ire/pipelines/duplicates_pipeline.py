from scrapy.crawler import Crawler

from open_ire.errors import DuplicateItemError
from open_ire.items import ArticleItem


class DuplicatesPipeline:
    """
    Drops duplicate items for a given spider session using the `reference` field.
    """

    def __init__(self) -> None:
        self.seen: set[str] = set()
        self.crawler: Crawler | None = None

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> "DuplicatesPipeline":
        pipeline = cls()
        pipeline.crawler = crawler
        return pipeline

    def open_spider(self) -> None:
        pass

    def process_item(self, item: ArticleItem) -> ArticleItem:
        if item.reference in self.seen:
            spider_name = (
                self.crawler.spider.name
                if self.crawler is not None and self.crawler.spider
                else "unknown"
            )
            raise DuplicateItemError(item.reference, spider_name)

        self.seen.add(item.reference)
        return item
