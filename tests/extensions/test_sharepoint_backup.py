import gzip
import logging
import os
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from msgraph.generated.models.drive_item import DriveItem
from msgraph.generated.models.file import File
from scrapy import signals
from scrapy.crawler import Crawler
from scrapy.exceptions import NotConfigured
from scrapy.settings import Settings

from open_ire.errors import ConfigurationError
from open_ire.extensions.sharepoint_backup import SharePointBackup, create_snapshot, stale_backups
from open_ire.sharepoint import SharePoint

SHAREPOINT_ENV = {
    "SHAREPOINT_CLIENT_ID": "test-client-id",
    "SHAREPOINT_TENANT_ID": "test-tenant-id",
    "SHAREPOINT_SITE_ID": "test-site-id",
    "SHAREPOINT_CLIENT_SECRET": "test-client-secret",
}


@pytest.fixture
def crawler() -> Crawler:
    mock_crawler = MagicMock(spec=Crawler)
    mock_crawler.settings = Settings()
    mock_crawler.signals = MagicMock()

    return mock_crawler


class TestSharePointBackup:
    """Tests the SharePoint backup extension."""

    @pytest.fixture
    def backup(self) -> SharePointBackup:
        with patch("open_ire.extensions.sharepoint_backup.SharePoint"):
            return SharePointBackup("test_sharepoint")

    def test_from_crawler(self, crawler: Crawler) -> None:
        crawler.settings.set("OPEN_IRE_DATABASE_FILE", "dbs/open_ire.db")
        crawler.settings.set("LOG_FILE", "output/run.log")
        crawler.settings.set("OPEN_IRE_SHAREPOINT_BASE_PATH", "test_sharepoint")
        crawler.settings.set("OPEN_IRE_SHAREPOINT_BACKUP_DIR", "test_backups")
        crawler.settings.set("OPEN_IRE_SHAREPOINT_BACKUP_DAYS_KEPT", 5)

        with (
            patch.dict(os.environ, SHAREPOINT_ENV, clear=True),
            patch("open_ire.extensions.sharepoint_backup.SharePoint", wraps=SharePoint) as cls,
            patch.object(SharePoint, "_authenticate"),
        ):
            backup = SharePointBackup.from_crawler(crawler)

        cls.assert_called_once_with(base_path="test_sharepoint")
        assert backup.db_path == Path("dbs/open_ire.db")
        assert backup.log_path == Path("output/run.log")
        assert backup.backup_dir == "test_backups"
        assert backup.backup_days_kept == 5

        cast(Any, crawler.signals.connect).assert_called_once_with(
            backup.spider_closed, signal=signals.spider_closed
        )

    @pytest.mark.parametrize(
        ("env", "missing"),
        [
            ({}, list(SHAREPOINT_ENV)),
            (
                {"SHAREPOINT_CLIENT_ID": "id", "SHAREPOINT_TENANT_ID": "tenant"},
                ["SHAREPOINT_SITE_ID", "SHAREPOINT_CLIENT_SECRET"],
            ),
            ({**SHAREPOINT_ENV, "SHAREPOINT_SITE_ID": ""}, ["SHAREPOINT_SITE_ID"]),
        ],
    )
    def test_from_crawler_disabled_without_credentials(
        self,
        crawler: Crawler,
        caplog: pytest.LogCaptureFixture,
        env: dict[str, str],
        missing: list[str],
    ) -> None:
        with (
            patch.dict(os.environ, env, clear=True),
            patch("open_ire.extensions.sharepoint_backup.SharePoint", wraps=SharePoint) as cls,
            caplog.at_level(logging.WARNING),
            pytest.raises(NotConfigured),
        ):
            SharePointBackup.from_crawler(crawler)

        cls.assert_not_called()
        cast(Any, crawler.signals.connect).assert_not_called()

        assert len(caplog.records) == 1
        message = caplog.records[0].getMessage()
        assert all(name in message for name in missing)
        assert all(value not in message for value in env.values() if value)

    def test_from_crawler_disabled_by_zero(self, crawler: Crawler) -> None:
        """Keeping zero days should disable the extension entirely."""
        crawler.settings.set("OPEN_IRE_SHAREPOINT_BACKUP_DAYS_KEPT", 0)

        with (
            patch.dict(os.environ, SHAREPOINT_ENV, clear=True),
            patch("open_ire.extensions.sharepoint_backup.SharePoint") as cls,
            pytest.raises(NotConfigured),
        ):
            SharePointBackup.from_crawler(crawler)

        cls.assert_not_called()
        cast(Any, crawler.signals.connect).assert_not_called()

    @pytest.mark.parametrize(("value", "expected"), [(None, None), (0, 0), (5, 5), ("5", 5)])
    def test_parse_backup_days_kept(self, value: Any, expected: int | None) -> None:
        assert SharePointBackup._parse_backup_days_kept(value) == expected

    @pytest.mark.parametrize("value", [-1, "-1", 2.5, "abc", "None", True])
    def test_parse_backup_days_kept_rejects_invalid(self, value: Any) -> None:
        with pytest.raises(ConfigurationError):
            SharePointBackup._parse_backup_days_kept(value)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("snapshot_uploaded", "pruned"), [(True, 1), (False, 0)])
    async def test_spider_closed(
        self, backup: SharePointBackup, snapshot_uploaded: bool, pruned: int
    ) -> None:
        """Both uploads should always run; pruning only after a good snapshot."""
        with (
            patch.object(
                backup, "_upload_database_backup", AsyncMock(return_value=snapshot_uploaded)
            ),
            patch.object(backup, "_upload_log_file", AsyncMock()) as upload_log,
            patch.object(backup, "_prune_old_backups", AsyncMock()) as prune,
        ):
            await backup.spider_closed(MagicMock(), "finished")

        upload_log.assert_awaited_once()
        assert prune.await_count == pruned

    @pytest.mark.asyncio
    async def test_upload_database_backup(self, backup: SharePointBackup, tmp_path: Path) -> None:
        db_path = _make_database(tmp_path / "open_ire.db", rows=10)
        backup.db_path = db_path
        backup.run_at = datetime(2026, 1, 27, 9, 30, 0, tzinfo=UTC)

        backup_path = "backups/open_ire__2026-01-27_09-30-00.db.gz"
        mock_upload_result = MagicMock()
        mock_upload_result.location = "https://sharepoint.com/uploaded-db"

        uploaded_bytes: list[bytes] = []

        async def capture_upload(local_path: Path, _remote_path: str) -> MagicMock:
            uploaded_bytes.append(local_path.read_bytes())
            sharepoint.get_item.return_value = DriveItem(
                file=File(), size=local_path.stat().st_size
            )
            return mock_upload_result

        sharepoint = cast(Any, backup.sharepoint)
        sharepoint.upload_file = AsyncMock(side_effect=capture_upload)
        sharepoint.get_item = AsyncMock()

        assert await backup._upload_database_backup() is True

        sharepoint.upload_file.assert_awaited_once()
        snapshot_path, remote_path = sharepoint.upload_file.await_args.args
        assert remote_path == backup_path
        assert snapshot_path.name == "open_ire.db.gz"
        assert uploaded_bytes[0][:2] == b"\x1f\x8b", "snapshot should be gzipped"
        assert not snapshot_path.exists(), "staging directory should be cleaned up"
        sharepoint.get_item.assert_awaited_once_with(backup_path)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "remote_state", ["verified", "missing", "wrong_size", "unknown_size", "folder", "error"]
    )
    async def test_snapshot_verification_controls_pruning(
        self, backup: SharePointBackup, tmp_path: Path, remote_state: str
    ) -> None:
        """An upload-session URL alone must never authorize pruning."""
        backup.db_path = _make_database(tmp_path / "open_ire.db", rows=10)
        sharepoint = cast(Any, backup.sharepoint)
        sharepoint.get_item = AsyncMock()

        async def capture_upload(local_path: Path, _remote_path: str) -> MagicMock:
            size = local_path.stat().st_size
            item = DriveItem(file=File(), size=size)
            if remote_state == "wrong_size":
                item.size = size + 1
            elif remote_state == "unknown_size":
                item.size = None
            elif remote_state == "folder":
                item.file = None
            elif remote_state == "error":
                sharepoint.get_item.side_effect = RuntimeError("metadata request failed")
            sharepoint.get_item.return_value = None if remote_state == "missing" else item
            return MagicMock(location="https://sharepoint.com/upload-session")

        sharepoint.upload_file = AsyncMock(side_effect=capture_upload)
        with (
            patch.object(backup, "_upload_log_file", AsyncMock()) as upload_log,
            patch.object(backup, "_prune_old_backups", AsyncMock()) as prune,
        ):
            await backup.spider_closed(MagicMock(), "finished")

        sharepoint.get_item.assert_awaited_once_with(backup._backup_path("db.gz"))
        upload_log.assert_awaited_once()
        assert prune.await_count == (1 if remote_state == "verified" else 0)

    @pytest.mark.asyncio
    async def test_upload_database_backup_failure(
        self, backup: SharePointBackup, tmp_path: Path
    ) -> None:
        """An upload without a location should be reported as a failure."""
        backup.db_path = _make_database(tmp_path / "open_ire.db", rows=10)
        mock_upload_result = MagicMock()
        mock_upload_result.location = None
        sharepoint = cast(Any, backup.sharepoint)
        sharepoint.upload_file = AsyncMock(return_value=mock_upload_result)

        assert await backup._upload_database_backup() is False

    @pytest.mark.asyncio
    async def test_upload_database_backup_snapshot_failure(
        self, backup: SharePointBackup, tmp_path: Path
    ) -> None:
        """A corrupt database should be reported without attempting an upload."""
        db_path = tmp_path / "open_ire.db"
        db_path.write_text("not-a-database")
        backup.db_path = db_path

        sharepoint = cast(Any, backup.sharepoint)
        sharepoint.upload_file = AsyncMock()

        assert await backup._upload_database_backup() is False

        sharepoint.upload_file.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_upload_log_file(self, backup: SharePointBackup, tmp_path: Path) -> None:
        """The run log should be uploaded as-is under this run's backup name."""
        log_path = tmp_path / "scrapy.log"
        log_path.write_text("INFO: crawl finished\n" * 100)
        backup.log_path = log_path
        backup.run_at = datetime(2026, 1, 27, 9, 30, 0, tzinfo=UTC)

        mock_upload_result = MagicMock()
        mock_upload_result.location = "https://sharepoint.com/uploaded-log"
        sharepoint = cast(Any, backup.sharepoint)
        sharepoint.upload_file = AsyncMock(return_value=mock_upload_result)

        await backup._upload_log_file()

        sharepoint.upload_file.assert_awaited_once_with(
            log_path, "backups/open_ire__2026-01-27_09-30-00.log"
        )

    @pytest.mark.asyncio
    async def test_upload_log_file_not_configured(self, backup: SharePointBackup) -> None:
        """No LOG_FILE setting should skip the upload rather than fail."""
        backup.log_path = None
        sharepoint = cast(Any, backup.sharepoint)
        sharepoint.upload_file = AsyncMock()

        await backup._upload_log_file()

        sharepoint.upload_file.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_upload_log_file_missing(self, backup: SharePointBackup, tmp_path: Path) -> None:
        """A configured but absent log file should skip the upload."""
        backup.log_path = tmp_path / "absent.log"
        sharepoint = cast(Any, backup.sharepoint)
        sharepoint.upload_file = AsyncMock()

        await backup._upload_log_file()

        sharepoint.upload_file.assert_not_awaited()

    def test_backup_path(self, backup: SharePointBackup) -> None:
        backup.run_at = datetime(2026, 2, 18, 9, 30, 5, tzinfo=UTC)

        assert backup._backup_path("db.gz") == "backups/open_ire__2026-02-18_09-30-05.db.gz"
        assert backup._backup_path("log") == "backups/open_ire__2026-02-18_09-30-05.log"

    @pytest.mark.asyncio
    async def test_prune_keeps_most_recent_days(self, backup: SharePointBackup) -> None:
        """Every backup from the newest days should survive; older days go."""
        backup.backup_days_kept = 2
        names = [
            "open_ire__2026-01-03_09-00-00.db.gz",
            "open_ire__2026-01-03_09-00-00.log",
            "open_ire__2026-01-03_15-00-00.db.gz",
            "open_ire__2026-01-03_15-00-00.log",
            "open_ire__2026-01-01_09-00-00.db.gz",
            "open_ire__2026-01-01_09-00-00.log",
            "open_ire__2026-01-02_09-00-00.db.gz",
            "open_ire__2026-01-02_09-00-00.log",
        ]
        sharepoint = cast(Any, backup.sharepoint)
        sharepoint.list_children = AsyncMock(return_value=[_drive_item(n) for n in names])
        sharepoint.delete_item = AsyncMock()

        await backup._prune_old_backups()

        sharepoint.list_children.assert_awaited_once_with("backups")
        deleted = sorted(call.args[0] for call in sharepoint.delete_item.await_args_list)
        assert deleted == [
            "backups/open_ire__2026-01-01_09-00-00.db.gz",
            "backups/open_ire__2026-01-01_09-00-00.log",
        ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("days_kept", [None, 0])
    async def test_prune_skipped_without_limit(
        self, backup: SharePointBackup, days_kept: int | None
    ) -> None:
        """None keeps every backup, and 0 must never prune everything."""
        backup.backup_days_kept = days_kept
        sharepoint = cast(Any, backup.sharepoint)
        sharepoint.list_children = AsyncMock()
        sharepoint.delete_item = AsyncMock()

        await backup._prune_old_backups()

        sharepoint.list_children.assert_not_awaited()
        sharepoint.delete_item.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_prune_survives_delete_failure(self, backup: SharePointBackup) -> None:
        """A failed delete should be logged without aborting the remaining prunes."""
        backup.backup_days_kept = 1
        names = [
            "open_ire__2026-01-03_09-00-00.db.gz",
            "open_ire__2026-01-02_09-00-00.db.gz",
            "open_ire__2026-01-01_09-00-00.db.gz",
        ]
        sharepoint = cast(Any, backup.sharepoint)
        sharepoint.list_children = AsyncMock(return_value=[_drive_item(n) for n in names])
        sharepoint.delete_item = AsyncMock(side_effect=[RuntimeError("boom"), None])

        await backup._prune_old_backups()

        assert sharepoint.delete_item.await_count == 2

    def test_create_snapshot_compacts_and_compresses(self, tmp_path: Path) -> None:
        """A snapshot should be gzipped and preserve the database contents."""
        db_path = _make_database(tmp_path / "open_ire.db", rows=500)
        staging = tmp_path / "staging"
        staging.mkdir()

        snapshot = create_snapshot(db_path, staging)

        assert snapshot.name == "open_ire.db.gz"
        assert list(staging.iterdir()) == [snapshot], "uncompressed copy should be removed"

        restored = tmp_path / "restored.db"
        with gzip.open(snapshot, "rb") as gz, restored.open("wb") as out:
            shutil.copyfileobj(gz, out)

        connection = sqlite3.connect(restored)
        try:
            assert connection.execute("SELECT count(*) FROM articles").fetchone()[0] == 500
        finally:
            connection.close()

    def test_create_snapshot_drops_free_pages(self, tmp_path: Path) -> None:
        """Vacuuming should reclaim space left behind by deleted rows."""
        db_path = _make_database(tmp_path / "open_ire.db", rows=2000)
        connection = sqlite3.connect(db_path)
        try:
            connection.execute("DELETE FROM articles WHERE id > 100")
            connection.commit()
            assert connection.execute("PRAGMA freelist_count").fetchone()[0] > 0
        finally:
            connection.close()
        staging = tmp_path / "staging"
        staging.mkdir()

        snapshot = create_snapshot(db_path, staging)

        assert snapshot.stat().st_size < db_path.stat().st_size

    def test_create_snapshot_leaves_source_untouched(self, tmp_path: Path) -> None:
        """The live database must not be modified by taking a snapshot."""
        db_path = _make_database(tmp_path / "open_ire.db", rows=100)
        before = db_path.read_bytes()
        staging = tmp_path / "staging"
        staging.mkdir()

        create_snapshot(db_path, staging)

        assert db_path.read_bytes() == before

    def test_stale_backups_keeps_most_recent_days(self) -> None:
        """Every backup from the newest days should survive; older days go."""
        names = [
            "open_ire__2026-01-03_09-00-00.db.gz",
            "open_ire__2026-01-03_15-00-00.log",
            "open_ire__2026-01-01_09-00-00.db.gz",
            "open_ire__2026-01-01_09-00-00.log",
            "open_ire__2026-01-02_09-00-00.db.gz",
        ]

        assert sorted(stale_backups(names, 2)) == [
            "open_ire__2026-01-01_09-00-00.db.gz",
            "open_ire__2026-01-01_09-00-00.log",
        ]

    def test_stale_backups_ignores_unrelated_files(self) -> None:
        """Files not named like our backups must never be stale."""
        names = [
            "open_ire__2026-01-02_09-00-00.db.gz",
            "open_ire__2026-01-01_09-00-00.db.gz",
            "open_ire_nursing__2026-01-01_09-00-00.db.gz",
            "open_ire__2026-01-01.db.gz",
            "open_ire__2026-01-01_09-00-00.log.gz",
            "open_ire__2026-01-01_09-00-00.db",
            "notes.txt",
        ]

        assert stale_backups(names, 1) == ["open_ire__2026-01-01_09-00-00.db.gz"]


def _make_database(db_path: Path, rows: int) -> Path:
    """Create a small SQLite database with a padded table for snapshot tests."""
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("CREATE TABLE articles (id INTEGER PRIMARY KEY, title TEXT)")
        connection.executemany(
            "INSERT INTO articles (id, title) VALUES (?, ?)",
            [(i, f"Article {i} " + "x" * 200) for i in range(1, rows + 1)],
        )
        connection.commit()
    finally:
        connection.close()

    return db_path


def _drive_item(name: str) -> MagicMock:
    """Stub a DriveItem; `name` is reserved by the MagicMock constructor."""
    item = MagicMock()
    item.name = name

    return item
