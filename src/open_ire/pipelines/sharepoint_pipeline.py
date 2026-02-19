import logging
import math
from datetime import datetime
from pathlib import Path
from posixpath import join as posix_join
from typing import Self

from scrapy import Spider, signals
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
        self,
        sharepoint_base_path: str,
        local_base_path: str,
        crawler: Crawler | None = None,
    ) -> None:
        self.sharepoint = SharePoint(base_path=sharepoint_base_path)
        self.base_path = Path(local_base_path)
        self.crawler = crawler
        self.db_path: Path | None = None

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> Self:
        if not (local_base_path := crawler.settings.get("FILES_STORE", "")):
            conf = "FILES_STORE"
            raise ConfigurationError(conf)

        sharepoint_base_path = crawler.settings.get("SHAREPOINT_BASE_PATH", "open_ire")
        db_path = crawler.settings.get("OPEN_IRE_DATABASE_FILE")
        pipeline = cls(sharepoint_base_path, local_base_path, crawler)
        pipeline.db_path = Path(db_path) if db_path else None
        crawler.signals.connect(pipeline._upload_database_backup, signal=signals.spider_closed)

        return pipeline

    @staticmethod
    def _remove_local_file(local_file_path: Path) -> None:
        try:
            local_file_path.unlink()
        except OSError as e:
            msg = f"Failed to remove local file {local_file_path}: {e}"
            logger.warning(msg)

    @staticmethod
    def _backup_filename(db_path: Path, run_at: datetime) -> str:
        date_stamp = run_at.strftime("%Y-%m-%d")
        return f"{db_path.stem}__{date_stamp}{db_path.suffix}"

    @staticmethod
    def _build_db_sharepoint_path(db_path: Path, run_at: datetime) -> str:
        filename = SharePointPipeline._backup_filename(db_path, run_at)
        backup_dir = db_path.parent.as_posix().strip("/")
        if backup_dir in ("", "."):
            return filename

        return posix_join(backup_dir, filename)

    async def _upload_database_backup(
        self,
        spider: Spider | None = None,
        reason: str = "completed",
    ) -> None:
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

    def open_spider(self) -> None:
        pass

    def close_spider(self) -> None:
        pass

    async def process_item(self, item: ArticleItem) -> ArticleItem:
        if not item.files:
            msg = f"No files found for article '{item.reference}'."
            logger.warning(msg)
            return item

        store_urls = []
        for file_data in item.files:
            store_urls.append(await self._save_file(file_data))

        item.store_urls = store_urls

        return item
