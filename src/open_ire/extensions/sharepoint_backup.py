import gzip
import logging
import re
import shutil
import sqlite3
import tempfile
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from posixpath import join as posix_join
from typing import Any, Self

from scrapy import Spider, signals
from scrapy.crawler import Crawler
from scrapy.exceptions import NotConfigured

from open_ire.errors import ConfigurationError
from open_ire.sharepoint import SharePoint

logger = logging.getLogger(__name__)

# Backups are named by the extension rather than after the local files, so
# pruning depends only on this format. Changing it orphans existing backups.
BACKUP_NAME = "open_ire__{run_at:%Y-%m-%d_%H-%M-%S}.{extension}"
BACKUP_NAME_PATTERN = re.compile(
    r"open_ire__(?P<date>\d{4}-\d{2}-\d{2})_\d{2}-\d{2}-\d{2}\.(?:db\.gz|log)"
)


class SharePointBackup:
    """
    Backs up the database and run log to a SharePoint drive when a spider
    closes, if configured.
    """

    def __init__(
        self,
        sharepoint_base_path: str,
        db_path: Path | None = None,
        log_path: Path | None = None,
        backup_days_kept: int | None = None,
        backup_dir: str = "backups",
    ) -> None:
        self.sharepoint = SharePoint(base_path=sharepoint_base_path)
        self.db_path = db_path
        self.log_path = log_path
        self.backup_days_kept = backup_days_kept
        self.backup_dir = backup_dir
        self.run_at = datetime.now().astimezone()

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> Self:
        if missing := SharePoint.missing_credentials():
            logger.warning("SharePoint backups disabled: missing or empty %s.", ", ".join(missing))
            raise NotConfigured

        backup_days_kept = cls._parse_backup_days_kept(
            crawler.settings.get("OPEN_IRE_SHAREPOINT_BACKUP_DAYS_KEPT")
        )
        if backup_days_kept == 0:
            logger.info("OPEN_IRE_SHAREPOINT_BACKUP_DAYS_KEPT is 0; skipping backups.")
            raise NotConfigured

        sharepoint_base_path = crawler.settings.get("OPEN_IRE_SHAREPOINT_BASE_PATH", "open_ire")
        db_path = crawler.settings.get("OPEN_IRE_DATABASE_FILE")
        log_file = crawler.settings.get("LOG_FILE")
        backup_dir = crawler.settings.get("OPEN_IRE_SHAREPOINT_BACKUP_DIR", "backups")
        backup = cls(
            sharepoint_base_path,
            Path(db_path) if db_path else None,
            Path(log_file) if log_file else None,
            backup_days_kept,
            backup_dir,
        )
        crawler.signals.connect(backup.spider_closed, signal=signals.spider_closed)

        return backup

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

    async def spider_closed(self, spider: Spider, reason: str) -> None:
        """Upload the database snapshot and run log, then prune old backups.

        Runs as a single handler because Scrapy runs async signal handlers
        concurrently, and pruning must follow both uploads. Pruning is
        skipped when the snapshot upload fails, so a run of failures can never
        prune away the last good snapshots.
        """
        logger.info("Backing up spider '%s' (reason=%s) to SharePoint", spider.name, reason)
        snapshot_uploaded = await self._upload_database_backup()
        await self._upload_log_file()
        if snapshot_uploaded:
            await self._prune_old_backups()

    async def _upload_database_backup(self) -> bool:
        if not self.db_path:
            logger.warning("OPEN_IRE_DATABASE_FILE is not configured; skipping DB backup upload.")
            return False

        if not self.db_path.exists():
            logger.warning("Database file not found: %s", self.db_path)
            return False

        with tempfile.TemporaryDirectory(prefix="open_ire_backup_") as staging_dir:
            try:
                snapshot_path = create_snapshot(self.db_path, Path(staging_dir))
            except (OSError, sqlite3.Error) as e:
                logger.error("Failed to snapshot database %s: %s", self.db_path, e)
                return False

            expected_size = snapshot_path.stat().st_size
            if not await self._upload_backup(snapshot_path, "db.gz"):
                return False

            sharepoint_path = self._backup_path("db.gz")
            try:
                item = await self.sharepoint.get_item(sharepoint_path)
            except Exception:
                logger.exception("Could not verify database backup: %s", sharepoint_path)
                return False

            if item is None or item.file is None or item.size != expected_size:
                logger.error(
                    "Database backup verification failed: %s (expected %d bytes)",
                    sharepoint_path,
                    expected_size,
                )
                return False

            return True

    async def _upload_log_file(self) -> None:
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
        await self._upload_backup(self.log_path, "log")

    async def _upload_backup(self, local_path: Path, extension: str) -> bool:
        """Upload a backup, logging failures without interrupting other backups."""
        sharepoint_path = self._backup_path(extension)
        try:
            logger.info(
                "Uploading backup to SharePoint: %s (%d bytes) -> %s",
                local_path,
                local_path.stat().st_size,
                sharepoint_path,
            )
            upload_result = await self.sharepoint.upload_file(local_path, sharepoint_path)
        except Exception:
            logger.exception("Failed to upload backup: %s -> %s", local_path, sharepoint_path)
            return False

        if not upload_result.location:
            logger.error("Failed to upload backup: %s", sharepoint_path)
            return False

        logger.info("Backup uploaded successfully: %s", sharepoint_path)

        return True

    def _backup_path(self, extension: str) -> str:
        """SharePoint path for this run's backup with the given ``extension``."""
        name = BACKUP_NAME.format(run_at=self.run_at, extension=extension)
        return posix_join(self.backup_dir, name)

    async def _prune_old_backups(self) -> None:
        """Delete backups from all but the most recent ``backup_days_kept`` days.

        See ``stale_backups`` for which files are considered.
        """
        # Zero disables backups in from_crawler; it must never mean "delete all".
        if not self.backup_days_kept:
            return

        try:
            children = await self.sharepoint.list_children(self.backup_dir)
        except Exception:
            logger.exception("Could not list SharePoint backups in '%s'", self.backup_dir)
            return

        names = (child.name or "" for child in children)
        for name in stale_backups(names, self.backup_days_kept):
            stale_path = posix_join(self.backup_dir, name)
            try:
                await self.sharepoint.delete_item(stale_path)
                logger.info("Pruned old backup: %s", stale_path)
            except Exception:
                logger.exception("Failed to prune backup %s", stale_path)


def create_snapshot(db_path: Path, dest_dir: Path) -> Path:
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

    compressed = dest_dir / f"{vacuumed.name}.gz"
    with vacuumed.open("rb") as raw, gzip.open(compressed, "wb", compresslevel=6) as gz:
        shutil.copyfileobj(raw, gz)
    vacuumed.unlink()

    return compressed


def stale_backups(names: Iterable[str], days_kept: int) -> list[str]:
    """Return the backups dated outside the most recent ``days_kept`` days.

    Only names matching ``BACKUP_NAME_PATTERN`` are considered, so anything
    else is never stale. A day's snapshots and logs are kept or dropped
    together. ``days_kept`` must be positive; zero would mark every backup
    stale.
    """
    backups_by_date: dict[str, list[str]] = defaultdict(list)
    for name in names:
        if match := BACKUP_NAME_PATTERN.fullmatch(name):
            backups_by_date[match["date"]].append(name)

    stale_dates = sorted(backups_by_date, reverse=True)[days_kept:]

    return [name for date in stale_dates for name in backups_by_date[date]]
