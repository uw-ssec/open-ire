from pathlib import Path
from urllib.parse import urlparse

from scrapy.crawler import Crawler
from scrapy.http import Request, Response
from scrapy.http.request import NO_CALLBACK
from scrapy.utils.defer import maybe_deferred_to_future

from open_ire.items import ArticleItem


class FileReferencePipeline:
    """
    Populates the `file_references` field of `ArticleItem` entities with metadata for external files.
    """

    def __init__(self, crawler: Crawler | None = None) -> None:
        self.crawler = crawler

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> "FileReferencePipeline":
        return cls(crawler)

    @staticmethod
    def _extract_file_size(response: Response) -> int | None:
        content_length = response.headers.get("Content-Length")
        if content_length:
            try:
                return int(content_length.decode())
            except (ValueError, AttributeError):
                pass

        return None

    @staticmethod
    def _extract_extension(url: str) -> str | None:
        parsed = urlparse(url)
        if path := parsed.path:
            extension = Path(path).suffix.lstrip(".")
            return extension.lower() if extension else None

        return None

    async def _get_file_reference(
        self, source_url: str, reference_url: str
    ) -> dict[str, str | int | None]:
        file_reference: dict[str, str | int | None] = {
            "extension": self._extract_extension(reference_url),
            "size": None,
            "source_url": source_url,
            "url": reference_url,
        }

        request = Request(reference_url, method="HEAD", callback=NO_CALLBACK)
        if self.crawler is None or not self.crawler.engine:
            return file_reference

        response = await maybe_deferred_to_future(self.crawler.engine.download(request))

        if response.status != 200:
            return file_reference

        file_reference["size"] = self._extract_file_size(response)

        return file_reference

    async def process_item(self, item: ArticleItem) -> ArticleItem:
        if self.crawler is None:
            msg = "Crawler context unavailable in FileReferencePipeline.process_item()."
            raise RuntimeError(msg)

        if not item.file_reference_urls:
            return item

        file_references = []
        for source_url, ref_url in item.file_reference_urls:
            file_reference = await self._get_file_reference(source_url, ref_url)
            file_references.append(file_reference)

        item.file_references = file_references

        return item
