"""Upload article files to SharePoint during a crawl."""

import logging
import math
from pathlib import Path
from typing import Any, Self

from scrapy.crawler import Crawler
from scrapy.exceptions import NotConfigured

from open_ire.errors import ConfigurationError
from open_ire.items import ArticleItem
from open_ire.sharepoint import SharePointClient

logger = logging.getLogger(__name__)


class SharePointPipeline:
    """Upload downloaded article files and record their SharePoint URLs.

    Remove local article files after confirming the remote size, and close the
    SharePoint client when the spider closes.
    """

    def __init__(self, sharepoint_base_path: str, files_store: str) -> None:
        """Create a pipeline using SharePoint credentials from the environment.

        Args:
            sharepoint_base_path: Destination folder relative to the SharePoint drive root.
            files_store: Local download directory containing the article files.

        Raises:
            ValueError: If required SharePoint credentials are missing.
        """
        self.sharepoint = SharePointClient(base_path=sharepoint_base_path)
        self.files_store = Path(files_store)

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> Self:
        """Create a pipeline configured from the crawler's settings.

        Read the local download directory from `FILES_STORE` and the remote
        folder from `OPEN_IRE_SHAREPOINT_BASE_PATH` (default: `open_ire`).

        Raises:
            NotConfigured: If required SharePoint environment variables are unset or empty.
            ConfigurationError: If `FILES_STORE` is unset or empty.
        """
        if missing := SharePointClient.missing_env_vars():
            logger.warning(
                "SharePoint uploads disabled: missing or empty %s. Files stay local.",
                ", ".join(missing),
            )
            raise NotConfigured

        if not (files_store := crawler.settings.get("FILES_STORE", "")):
            conf = "FILES_STORE"
            raise ConfigurationError(conf)

        sharepoint_base_path = crawler.settings.get("OPEN_IRE_SHAREPOINT_BASE_PATH", "open_ire")

        return cls(sharepoint_base_path, files_store)

    async def _save_file(self, file_data: dict[str, str | int | None]) -> str:
        """Upload an article file and remove its local copy if the sizes are close.

        Sizes match within a relative tolerance of 1% or an absolute tolerance
        of 1 KiB. A mismatch retains the local file and logs an error. Upload,
        metadata lookup, and cleanup exceptions are logged instead of raised.

        Args:
            file_data: Download metadata whose `path` is relative to both the
                local download directory and the SharePoint base folder.

        Returns:
            The remote web URL if retrieved, even if size verification or local
            cleanup fails. Otherwise, an empty string.
        """
        sharepoint_path = str(file_data.get("path") or "")
        local_file_path = self.files_store / sharepoint_path

        if not local_file_path.exists():
            logger.error("Local file not found: %s", local_file_path)
            return ""

        store_url = ""
        try:
            logger.info("Uploading file to SharePoint: %s -> %s", local_file_path, sharepoint_path)
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
                    logger.error(
                        "Local file size (%s) does not match remote (%s)",
                        local_file_path,
                        store_url,
                    )

        except Exception as e:
            logger.error("Error uploading file %s: %s", local_file_path, e)

        return store_url

    async def process_item(self, item: Any) -> Any:
        """Upload an article's files sequentially and replace its `store_urls`.

        Each file contributes a URL or an empty string, preserving file order.
        Pass through non-article items and articles without files unchanged.

        Returns:
            The original item, with `store_urls` updated when files are present.
        """
        if not isinstance(item, ArticleItem):
            return item
        if not item.files:
            logger.warning("No files found for article '%s'.", item.reference)
            return item

        store_urls = []
        for file_data in item.files:
            store_urls.append(await self._save_file(file_data))

        item.store_urls = store_urls

        return item

    async def close_spider(self) -> None:
        """Close the SharePoint client once no more items will arrive."""
        await self.sharepoint.close()
