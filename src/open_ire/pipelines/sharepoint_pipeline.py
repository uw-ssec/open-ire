"""Upload article files and a database backup to SharePoint during a crawl."""

import logging
import math
from datetime import datetime
from pathlib import Path
from posixpath import join as posix_join
from typing import Any, Self

from scrapy import Spider, signals
from scrapy.crawler import Crawler
from scrapy.exceptions import NotConfigured

from open_ire.errors import ConfigurationError
from open_ire.items import ArticleItem
from open_ire.sharepoint import SharePointClient

logger = logging.getLogger(__name__)


class SharePointPipeline:
    """Upload downloaded article files and record their SharePoint URLs.

    Remove local article files after confirming the remote size. When created
    through `from_crawler()`, also upload a dated database backup on spider
    shutdown and close the SharePoint client afterward.
    """

    def __init__(
        self,
        sharepoint_base_path: str,
        local_base_path: str,
        crawler: Crawler | None = None,
    ) -> None:
        """Create a pipeline using SharePoint credentials from the environment.

        Args:
            sharepoint_base_path: Destination folder relative to the SharePoint drive root.
            local_base_path: Local download directory containing the article files.
            crawler: Scrapy crawler associated with this pipeline, if available.

        Raises:
            ValueError: If required SharePoint credentials are missing.
        """
        self.sharepoint = SharePointClient(base_path=sharepoint_base_path)
        self.local_base_path = Path(local_base_path)
        self.crawler = crawler
        self.db_path: Path | None = None

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> Self:
        """Create a configured pipeline and register its spider shutdown handler.

        Read the local download directory from `FILES_STORE` and the remote
        folder from `OPEN_IRE_SHAREPOINT_BASE_PATH` (default: `open_ire`).
        `OPEN_IRE_DATABASE_FILE`, when set, identifies the database to back up.

        Raises:
            NotConfigured: If required SharePoint environment variables are unset or empty.
            ConfigurationError: If `FILES_STORE` is unset or empty.
        """
        if missing := SharePointClient.missing_env_vars():
            logger.warning(
                "SharePoint integration disabled: missing or empty %s. "
                "Files stay local and the database backup upload is skipped.",
                ", ".join(missing),
            )
            raise NotConfigured

        if not (local_base_path := crawler.settings.get("FILES_STORE", "")):
            conf = "FILES_STORE"
            raise ConfigurationError(conf)

        sharepoint_base_path = crawler.settings.get("OPEN_IRE_SHAREPOINT_BASE_PATH", "open_ire")
        db_path = crawler.settings.get("OPEN_IRE_DATABASE_FILE")
        pipeline = cls(sharepoint_base_path, local_base_path, crawler)
        pipeline.db_path = Path(db_path) if db_path else None
        crawler.signals.connect(pipeline._on_spider_closed, signal=signals.spider_closed)

        return pipeline

    @staticmethod
    def _remove_local_file(local_file_path: Path) -> None:
        """Remove a local file, logging any filesystem error instead of raising it."""
        try:
            local_file_path.unlink()
        except OSError as e:
            msg = f"Failed to remove local file {local_file_path}: {e}"
            logger.warning(msg)

    @staticmethod
    def _backup_filename(db_path: Path, run_at: datetime) -> str:
        """Insert `__YYYY-MM-DD` before the database filename's final extension."""
        date_stamp = run_at.strftime("%Y-%m-%d")
        return f"{db_path.stem}__{date_stamp}{db_path.suffix}"

    @staticmethod
    def _build_db_sharepoint_path(db_path: Path, run_at: datetime) -> str:
        """Build a dated backup path relative to the SharePoint base folder.

        Preserve the database's parent directories, stripping leading and
        trailing slashes so absolute local paths become remote relative paths.
        """
        filename = SharePointPipeline._backup_filename(db_path, run_at)
        backup_dir = db_path.parent.as_posix().strip("/")
        if backup_dir in ("", "."):
            return filename

        return posix_join(backup_dir, filename)

    async def _on_spider_closed(
        self,
        spider: Spider | None = None,
        reason: str = "completed",
    ) -> None:
        """Upload the database backup, then close the client even if the upload fails.

        Args:
            spider: Closed spider, used for backup failure logging.
            reason: Spider shutdown reason, used for backup failure logging.
        """
        # Scrapy calls close_spider before this signal, and it runs signal handlers concurrently,
        # so the client can only be closed here, once the backup is done.
        try:
            await self._upload_database_backup(spider, reason)
        finally:
            await self.sharepoint.close()

    async def _upload_database_backup(
        self,
        spider: Spider | None = None,
        reason: str = "completed",
    ) -> None:
        """Upload the database under a name dated with the current local time.

        Keep the local database. Repeated uploads on the same day replace the
        remote backup. Log and skip an unconfigured or missing database; log
        missing upload confirmation, and let client exceptions propagate.

        Args:
            spider: Spider whose name is included when the upload has no location.
            reason: Shutdown reason included when the upload has no location.
        """
        if not self.db_path:
            logger.warning("OPEN_IRE_DATABASE_FILE is not configured; skipping DB backup upload.")
            return

        local_db_path = self.db_path
        if not local_db_path.exists():
            logger.warning("Database file not found: %s", local_db_path)
            return

        backup_time = datetime.now()
        sharepoint_path = self._build_db_sharepoint_path(local_db_path, backup_time)
        logger.info(
            "Uploading database snapshot to SharePoint: %s -> %s",
            local_db_path,
            sharepoint_path,
        )

        upload_result = await self.sharepoint.upload_file(local_db_path, sharepoint_path)
        if not upload_result.location:
            spider_name = spider.name if spider else "unknown"
            logger.error(
                "Failed to upload database snapshot for spider '%s' (reason=%s): %s",
                spider_name,
                reason,
                local_db_path,
            )
            return

        drive_item = await self.sharepoint.get_item(sharepoint_path)
        if not drive_item:
            logger.error("Could not confirm SharePoint database snapshot: %s", sharepoint_path)
            return

        logger.info(
            "Database snapshot uploaded successfully: %s",
            drive_item.web_url or sharepoint_path,
        )

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
        local_file_path = self.local_base_path / sharepoint_path

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

    def open_spider(self) -> None:
        """Accept Scrapy's open hook; initialization needs no additional setup."""

    def close_spider(self) -> None:
        """Leave cleanup to the spider-closed signal so the backup can finish first."""

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
            msg = f"No files found for article '{item.reference}'."
            logger.warning(msg)
            return item

        store_urls = []
        for file_data in item.files:
            store_urls.append(await self._save_file(file_data))

        item.store_urls = store_urls

        return item
