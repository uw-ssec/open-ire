from __future__ import annotations

import json
from pathlib import Path

from itemadapter import ItemAdapter
from scrapy import Item, Spider
from scrapy.exceptions import DropItem

# Remember to add your pipeline to the ITEM_PIPELINES setting


class DuplicatesPipeline:
    def __init__(self):
        self.seen = set()

    def process_item(self, item: Item, spider: Spider):
        adapter = ItemAdapter(item)

        if adapter["reference"] in self.seen:
            exception_msg = f"Item ID already seen: {adapter['reference']} by {spider.name} spider"
            raise DropItem(exception_msg)

        self.seen.add(adapter["reference"])
        return item


class JsonWriterPipeline:
    def __init__(self):
        self.file = None

    def open_spider(self, spider: Spider):
        self.file = Path(f"output/{spider.name}_items.jsonl").open("w")  # noqa: SIM115

    def close_spider(self, spider: Spider):  # noqa: ARG002
        self.file.close()

    def process_item(self, item: Item, spider: Spider) -> Item:  # noqa: ARG002
        line = json.dumps(ItemAdapter(item).asdict()) + "\n"
        self.file.write(line)

        return item
