import json

from itemadapter import ItemAdapter
from scrapy import Spider, Item
from scrapy.exceptions import DropItem


# Remember to add your pipeline to the ITEM_PIPELINES setting

class DuplicatesPipeline:
    def __init__(self):
        self.seen = set()

    def process_item(self, item: Item, spider: Spider):
        adapter = ItemAdapter(item)

        if adapter["reference"] in self.seen:
            raise DropItem(f"Item ID already seen: {adapter['reference']} by {spider.name} spider")
        else:
            self.seen.add(adapter["reference"])
            return item


class JsonWriterPipeline:
    def __init__(self):
        self.file = None

    def open_spider(self, spider: Spider):
        self.file = open(f"output/{spider.name}_items.jsonl", "w")

    def close_spider(self, spider: Spider):
        self.file.close()

    def process_item(self, item: Item, spider: Spider) -> Item:
        line = json.dumps(ItemAdapter(item).asdict()) + "\n"
        self.file.write(line)

        return item
