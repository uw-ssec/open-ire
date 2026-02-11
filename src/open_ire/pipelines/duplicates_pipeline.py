from scrapy import Spider

from open_ire.errors import DuplicateItemError
from open_ire.items import ArticleItem


class DuplicatesPipeline:
    """
    Drops duplicate items for a given spider session using the `reference` field.
    """

    def __init__(self) -> None:
        self.seen: set[str] = set()

    def process_item(self, item: ArticleItem, spider: Spider) -> ArticleItem:
        if item.reference in self.seen:
            raise DuplicateItemError(item.reference, spider.name)

        self.seen.add(item.reference)
        return item
