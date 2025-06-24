from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import TextIO

from scrapy import Spider
from scrapy.exceptions import DropItem

from open_ire.items import OpenIreItem

# Remember to add your pipelines to the `settings.ITEM_PIPELINES` list


class DuplicatesPipeline:
    def __init__(self) -> None:
        self.seen: set[str] = set()

    def process_item(self, item: OpenIreItem, spider: Spider) -> OpenIreItem:
        if item.reference in self.seen:
            drop_reason = f"Item ID already seen: {item.reference} by {spider.name} spider"
            raise DropItem(drop_reason)
        self.seen.add(item.reference)
        return item


class JsonWriterPipeline:
    def __init__(self) -> None:
        self.file: TextIO | None = None

    def open_spider(self, spider: Spider) -> None:
        out_dir = Path("output")
        out_dir.mkdir(parents=True, exist_ok=True)

        self.file = (out_dir / f"{spider.name}_items.jsonl").open("w", encoding="utf-8")

    def close_spider(self, spider: Spider) -> None:  # noqa: ARG002
        if self.file is not None:
            self.file.close()

    def process_item(self, item: OpenIreItem, spider: Spider) -> OpenIreItem:  # noqa: ARG002
        assert self.file is not None, "Pipeline not opened before processing items"

        line = json.dumps(dataclasses.asdict(item)) + "\n"
        self.file.write(line)
        return item
