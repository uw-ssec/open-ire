import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Self

from scrapy import Spider
from scrapy.crawler import Crawler
from scrapy.http import HtmlResponse, Response

from open_ire.errors import ConfigurationError


class SaveResponsesMiddleware:
    """
    A spider middleware to save all responses and their request metadata to files
    for offline testing.
    """

    def __init__(self, save_dir: Path):
        self.save_dir = save_dir
        self.save_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> Self:
        """Create an instance of the middleware from crawler settings."""
        config = "SAVE_RESPONSES_DIR"
        save_dir_str = crawler.settings.get(config)
        if not save_dir_str:
            raise ConfigurationError(config)

        return cls(save_dir=Path(save_dir_str))

    def process_spider_input(self, response: Response, spider: Spider) -> None:
        """Save the response body and request metadata to files."""
        # Create a stable filename from the response URL
        url_hash = hashlib.sha1(response.url.encode("utf-8")).hexdigest()
        timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        base_path = self.save_dir / f"{spider.name}_{timestamp}_{url_hash}"

        # 1. Save the response content
        if isinstance(response, HtmlResponse):
            response_path = base_path.with_suffix(".html")
        else:
            response_path = base_path.with_suffix(".txt")
        response_path.write_bytes(response.body)

        # 2. Save the request metadata
        metadata = {
            "url": response.url,
            "method": response.request.method if response.request else "GET",
            "status": response.status,
            "headers": dict(response.request.headers.to_unicode_dict()) if response.request else {},
            "cb_kwargs": response.request.cb_kwargs if response.request else {},
        }
        meta_path = base_path.with_suffix(".json")
        meta_path.write_text(json.dumps(metadata, indent=2))

        spider.logger.info(
            "Saved response for URL %s to %s and %s",
            response.url,
            response_path,
            meta_path,
        )

        # Let the response continue through the processing chain
        return  # noqa: PLR1711
