import gzip
import logging
import math
import re
import shutil
import sqlite3
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from posixpath import join as posix_join
from typing import Any, Self

from scrapy import Spider, signals
from scrapy.crawler import Crawler
from scrapy.exceptions import NotConfigured

from open_ire.errors import ConfigurationError
from open_ire.items import ArticleItem
from open_ire.sharepoint import SharePoint

logger = logging.getLogger(__name__)

# Backups are named by the pipeline rather than after the local files, so
# pruning depends only on this format. Changing it orphans existing backups.
BACKUP_NAME = "open_ire__{run_at:%Y-%m-%d_%H-%M-%S}.{extension}"
BACKUP_NAME_PATTERN = re.compile(
    r"open_ire__(?P<date>\d{4}-\d{2}-\d{2})_\d{2}-\d{2}-\d{2}\.(?:db\.gz|log)"
)


class SharePointPipeline:
    """
    Uploads files to a SharePoint drive, if configured.
    """

    def __init__(
        self,
        sharepoint_base_path: str,
        local_base_path: str,
        crawler: Crawler | None = None,
        backup_days_kept: int | None = None,
        backup_dir: str = "backups",
    ) -> None:
        self.sharepoint = SharePoint(base_path=sharepoint_base_path)
        self.base_path = Path(local_base_path)
        self.crawler = crawler
        self.backup_days_kept = backup_days_kept
        self.backup_dir = backup_dir
        self.run_at = datetime.now()
        self.db_path: Path | None = None
        self.log_path: Path | None = None

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> Self:
        if missing := SharePoint.missing_credentials():
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
        log_file = crawler.settings.get("LOG_FILE")
        backup_days_kept = cls._parse_backup_days_kept(
            crawler.settings.get("OPEN_IRE_SHAREPOINT_BACKUP_DAYS_KEPT")
        )
        backup_dir = crawler.settings.get("OPEN_IRE_SHAREPOINT_BACKUP_DIR", "backups")
        pipeline = cls(sharepoint_base_path, local_base_path, crawler, backup_days_kept, backup_dir)
        pipeline.db_path = Path(db_path) if db_path else None
        pipeline.log_path = Path(log_file) if log_file else None
        crawler.signals.connect(pipeline._back_up_run, signal=signals.spider_closed)

        return pipeline

    @staticmethod
    def _parse_backup_days_kept(value: Any) -> int | None:
        """Validate ``OPEN_IRE_SHAREPOINT_BACKUP_DAYS_KEPT``.

        ``None`` keeps every backup and ``0`` disables backups. A digit string
        is accepted so the setting can be overridden with ``scrapy crawl -s``.
        """
        if value is None:
            return None
        if isinstance(value, str) and value.isdigit():
            return int(value)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value

        conf = "OPEN_IRE_SHAREPOINT_BACKUP_DAYS_KEPT as None or a non-negative integer"
        raise ConfigurationError(conf)

    @staticmethod
    def _remove_local_file(local_file_path: Path) -> None:
        try:
            local_file_path.unlink()
        except OSError as e:
            msg = f"Failed to remove local file {local_file_path}: {e}"
            logger.warning(msg)

    @staticmethod
    def _gzip_file(source: Path, dest_dir: Path) -> Path:
        """Write a gzipped copy of ``source`` into ``dest_dir``."""
        compressed = dest_dir / f"{source.name}.gz"
        with source.open("rb") as raw, gzip.open(compressed, "wb", compresslevel=6) as gz:
            shutil.copyfileobj(raw, gz)

        return compressed

    @staticmethod
    def _create_snapshot(db_path: Path, dest_dir: Path) -> Path:
        """Write a compacted, gzipped copy of the database into ``dest_dir``.

        ``VACUUM INTO`` reads through a read-only connection and produces a
        transactionally consistent copy, so the live database is never held
        open for writing and free pages are dropped from the result.

        Parameters
        ----------
        db_path
            Path to the live SQLite database.
        dest_dir
            Directory to write the snapshot into; must already exist.

        Returns
        -------
        Path to the gzipped snapshot.
        """
        vacuumed = dest_dir / db_path.name
        connection = sqlite3.connect(f"{db_path.absolute().as_uri()}?mode=ro", uri=True)
        try:
            connection.execute("VACUUM INTO ?", (str(vacuumed),))
        finally:
            connection.close()

        compressed = SharePointPipeline._gzip_file(vacuumed, dest_dir)
        vacuumed.unlink()

        return compressed

    def _backup_path(self, extension: str) -> str:
        """SharePoint path for this run's backup with the given ``extension``."""
        name = BACKUP_NAME.format(run_at=self.run_at, extension=extension)
        return posix_join(self.backup_dir, name)

    async def _prune_old_backups(self) -> None:
        """Delete backups from all but the most recent ``backup_days_kept`` days.

        Only files matching ``BACKUP_NAME_PATTERN`` are considered, so anything
        else in the backup folder is left alone. A day's snapshots and logs are
        kept or deleted together.
        """
        # Zero disables backups in _back_up_run; it must never mean "delete all".
        if not self.backup_days_kept:
            return

        try:
            children = await self.sharepoint.list_children(self.backup_dir)
        except Exception:
            logger.exception("Could not list SharePoint backups in '%s'", self.backup_dir)
            return

        backups_by_date: dict[str, list[str]] = defaultdict(list)
        for child in children:
            name = child.name or ""
            if match := BACKUP_NAME_PATTERN.fullmatch(name):
                backups_by_date[match["date"]].append(name)

        stale_dates = sorted(backups_by_date, reverse=True)[self.backup_days_kept :]
        for date in stale_dates:
            for name in backups_by_date[date]:
                stale_path = posix_join(self.backup_dir, name)
                try:
                    await self.sharepoint.delete_item(stale_path)
                    logger.info("Pruned old backup: %s", stale_path)
                except Exception:
                    logger.exception("Failed to prune backup %s", stale_path)

    async def _back_up_run(
        self,
        spider: Spider | None = None,
        reason: str = "completed",
    ) -> None:
        """Upload the database snapshot and run log, then prune old backups.

        Runs as a single ``spider_closed`` handler because Scrapy runs async
        handlers concurrently, and pruning must follow both uploads. Pruning is
        skipped when the snapshot upload fails, so a run of failures can never
        prune away the last good snapshots.
        """
        if self.backup_days_kept == 0:
            logger.info("OPEN_IRE_SHAREPOINT_BACKUP_DAYS_KEPT is 0; skipping backups.")
            return

        snapshot_uploaded = await self._upload_database_backup(spider, reason)
        await self._upload_log_file(spider, reason)
        if snapshot_uploaded:
            await self._prune_old_backups()

    async def _upload_database_backup(
        self,
        spider: Spider | None = None,
        reason: str = "completed",
    ) -> bool:
        if not self.db_path:
            logger.warning("OPEN_IRE_DATABASE_FILE is not configured; skipping DB backup upload.")
            return False

        local_db_path = self.db_path
        if not local_db_path.exists():
            logger.warning("Database file not found: %s", local_db_path)
            return False

        sharepoint_path = self._backup_path("db.gz")

        with tempfile.TemporaryDirectory(prefix="open_ire_backup_") as staging_dir:
            try:
                snapshot_path = self._create_snapshot(local_db_path, Path(staging_dir))
            except (OSError, sqlite3.Error) as e:
                logger.error("Failed to snapshot database %s: %s", local_db_path, e)
                return False

            logger.info(
                "Uploading database snapshot to SharePoint: %s (%d bytes) -> %s",
                local_db_path,
                snapshot_path.stat().st_size,
                sharepoint_path,
            )

            upload_result = await self.sharepoint.upload_file(snapshot_path, sharepoint_path)

        if not upload_result.location:
            spider_name = spider.name if spider else "unknown"
            logger.error(
                "Failed to upload database snapshot for spider '%s' (reason=%s): %s",
                spider_name,
                reason,
                local_db_path,
            )
            return False

        # The upload itself succeeded, so a failed confirmation is only logged.
        drive_item = await self.sharepoint.get_item(sharepoint_path)
        if not drive_item:
            logger.warning("Could not confirm SharePoint database snapshot: %s", sharepoint_path)
            return True

        logger.info(
            "Database snapshot uploaded successfully: %s",
            drive_item.web_url or sharepoint_path,
        )

        return True

    async def _upload_log_file(
        self,
        spider: Spider | None = None,
        reason: str = "completed",
    ) -> None:
        if not self.log_path:
            logger.debug("LOG_FILE is not configured; skipping log upload.")
            return

        if not self.log_path.exists():
            logger.warning("Log file not found: %s", self.log_path)
            return

        # Scrapy tears down its log handlers only after spider_closed, so flush
        # them here to avoid uploading a truncated log.
        for handler in logging.getLogger().handlers:
            handler.flush()

        # Uploaded uncompressed so it can be read in the SharePoint web viewer.
        sharepoint_path = self._backup_path("log")
        logger.info("Uploading run log to SharePoint: %s -> %s", self.log_path, sharepoint_path)
        upload_result = await self.sharepoint.upload_file(self.log_path, sharepoint_path)

        if not upload_result.location:
            spider_name = spider.name if spider else "unknown"
            logger.error(
                "Failed to upload run log for spider '%s' (reason=%s): %s",
                spider_name,
                reason,
                self.log_path,
            )
            return

        logger.info("Run log uploaded successfully: %s", sharepoint_path)

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
