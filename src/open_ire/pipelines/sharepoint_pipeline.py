import logging
import math
from pathlib import Path
from typing import Any, Self

from scrapy.crawler import Crawler

from open_ire.errors import ConfigurationError
from open_ire.items import ArticleItem
from open_ire.sharepoint import SharePoint

logger = logging.getLogger(__name__)


class SharePointPipeline:
    """
    Uploads files to a SharePoint drive, if configured.
    """

    def __init__(
        self, sharepoint_base_path: str, local_base_path: str, crawler: Crawler | None = None
    ) -> None:
        self.sharepoint = SharePoint(base_path=sharepoint_base_path)
        self.base_path = Path(local_base_path)
        self.crawler = crawler

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> Self:
        if not (local_base_path := crawler.settings.get("FILES_STORE", "")):
            conf = "FILES_STORE"
            raise ConfigurationError(conf)

        sharepoint_base_path = crawler.settings.get("SHAREPOINT_BASE_PATH", "open_ire")

        return cls(sharepoint_base_path, local_base_path, crawler)

    @staticmethod
    def _remove_local_file(local_file_path: Path) -> None:
        try:
            local_file_path.unlink()
        except OSError as e:
            msg = f"Failed to remove local file {local_file_path}: {e}"
            logger.warning(msg)

    def open_spider(self) -> None:
        pass

    def close_spider(self) -> None:
        pass

    async def _save_file(self, file_data: dict[str, str | int | None]) -> str:
        sharepoint_path = str(file_data.get("path") or "")
        local_file_path = self.base_path / sharepoint_path

        if not local_file_path.exists():
            msg = f"Local file not found: {local_file_path}"
            logger.error(msg)
            return ""

        store_url = ""
        try:
            msg = f"Uploading file to SharePoint: {local_file_path} -> {sharepoint_path}"
            logger.info(msg)

            upload_result = await self.sharepoint.upload_file(local_file_path, sharepoint_path)
            if upload_result.location:
                drive_item = await self.sharepoint.get_item(sharepoint_path)

                if not drive_item:
                    msg = f"Failed to confirm SharePoint upload: {local_file_path}"
                    raise RuntimeError(msg)

                if drive_item.web_url:
                    store_url = drive_item.web_url

                local_size = local_file_path.stat().st_size
                remote_size = drive_item.size or 0.0
                if math.isclose(local_size, remote_size, rel_tol=0.01, abs_tol=1024.0):
                    local_file_path.unlink()
                else:
                    msg = f"Local file size ({local_file_path}) does not match remote ({store_url})"
                    logger.error(msg)

        except Exception as e:
            msg = f"Error uploading file {local_file_path}: {e}"
            logger.error(msg)

        return store_url

    async def process_item(self, item: Any) -> Any:
        if not isinstance(item, ArticleItem):
            return item

        if not item.files:
            msg = f"No files found for article '{item.reference}'."
            logger.warning(msg)
            return item

        store_urls = []
        for file_data in item.files:
            store_urls.append(await self._save_file(file_data))

        item.store_urls = store_urls

        return item
