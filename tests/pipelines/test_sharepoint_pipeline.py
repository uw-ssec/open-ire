import gzip
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from scrapy import signals
from scrapy.crawler import Crawler

from open_ire.items import ArticleItem
from open_ire.pipelines import SharePointPipeline


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


class TestSharePointPipeline:
    """Tests the SharePoint pipeline for file uploads."""

    @pytest.fixture
    def pipeline(self, crawler: Crawler, tmp_path: Path) -> SharePointPipeline:
        sharepoint_base_path = "test_sharepoint"
        local_base_path = str(tmp_path)

        with patch("open_ire.pipelines.sharepoint_pipeline.SharePoint") as mock_sharepoint_class:
            mock_sharepoint = MagicMock()
            mock_sharepoint_class.return_value = mock_sharepoint

            pipeline = SharePointPipeline(sharepoint_base_path, local_base_path)
            pipeline.sharepoint = mock_sharepoint
            pipeline.crawler = crawler
            pipeline.open_spider()
            assert pipeline.crawler is not None
            assert pipeline.crawler.spider is not None

            return pipeline

    @pytest.mark.asyncio
    async def test_passes_through_non_article_items(self, pipeline: SharePointPipeline) -> None:
        """Test that non-ArticleItem items are passed through unchanged."""
        item = MagicMock(spec=Any)
        result = await pipeline.process_item(item)

        assert result is item

    @pytest.mark.asyncio
    async def test_item_with_files(
        self, pipeline: SharePointPipeline, item: ArticleItem, tmp_path: Path
    ) -> None:
        """An item with files should trigger a SharePoint upload."""
        file1 = tmp_path / "file1.pdf"
        file2 = tmp_path / "file2.pdf"
        file1.write_text("content1")
        file2.write_text("content2")

        item.files = [
            {"path": "file1.pdf", "url": "https://example.com/file1.pdf"},
            {"path": "file2.pdf", "url": "https://example.com/file2.pdf"},
        ]

        mock_upload_result = MagicMock()
        mock_upload_result.location = "https://sharepoint.com/uploaded"
        sharepoint = cast(Any, pipeline.sharepoint)
        sharepoint.upload_file = AsyncMock(return_value=mock_upload_result)

        mock_drive_item = MagicMock()
        mock_drive_item.web_url = "https://sharepoint.com/web-url"
        sharepoint.get_item = AsyncMock(return_value=mock_drive_item)

        result = await pipeline.process_item(item)

        assert result == item
        assert result.store_urls == [
            "https://sharepoint.com/web-url",
            "https://sharepoint.com/web-url",
        ]
        assert sharepoint.upload_file.call_count == 2

    @pytest.mark.asyncio
    async def test_process_item_no_files(
        self, pipeline: SharePointPipeline, item: ArticleItem
    ) -> None:
        """An item without files should have an empty store_urls list."""
        item.files = []

        result = await pipeline.process_item(item)

        assert result == item
        assert result.store_urls == []

    @pytest.mark.asyncio
    async def test_item_upload_error(self, pipeline: SharePointPipeline, item: ArticleItem) -> None:
        """Upload errors should be logged."""
        sharepoint = cast(Any, pipeline.sharepoint)
        sharepoint.upload_file = AsyncMock(side_effect=Exception("Upload failed"))

        result = await pipeline.process_item(item)

        assert result == item
        assert result.store_urls == [""]

    @pytest.mark.asyncio
    async def test_deletes_local_file(
        self, pipeline: SharePointPipeline, item: ArticleItem, tmp_path: Path
    ) -> None:
        """Local file should be deleted when remote size matches"""
        local_file = tmp_path / "file.pdf"
        local_file.write_text("A" * 1000)
        item.files = [{"path": "file.pdf", "url": "https://example.com/file.pdf"}]

        # Match
        mock_drive_item = MagicMock()
        mock_drive_item.web_url = "https://sharepoint.com/uploaded"
        mock_drive_item.size = 1000
        sharepoint = cast(Any, pipeline.sharepoint)
        sharepoint.upload_file = AsyncMock(return_value=MagicMock())
        sharepoint.get_item = AsyncMock(return_value=mock_drive_item)

        await pipeline.process_item(item)
        assert not local_file.exists()  # delete local copy

        # Mismatch
        local_file.write_text("B" * 2000)
        mock_drive_item.size = 5000
        await pipeline.process_item(item)
        assert local_file.exists()  # preserve local copy

    def test_db_snapshot_on_spider_close(self, crawler: Crawler, tmp_path: Path) -> None:
        crawler.settings.set("FILES_STORE", str(tmp_path))
        crawler.settings.set("OPEN_IRE_DATABASE_FILE", "dbs/open_ire.db")
        crawler.settings.set("OPEN_IRE_SHAREPOINT_BASE_PATH", "test_sharepoint")
        crawler.signals = MagicMock()

        with patch("open_ire.pipelines.sharepoint_pipeline.SharePoint"):
            pipeline = SharePointPipeline.from_crawler(crawler)

        assert pipeline.db_path == Path("dbs/open_ire.db")

        connect = cast(Any, crawler.signals.connect)
        connect.assert_called_once()
        assert connect.call_args.kwargs["signal"] == signals.spider_closed
        assert connect.call_args.args[0] == pipeline._upload_database_backup

    def test_build_db_sharepoint_path(self, pipeline: SharePointPipeline) -> None:
        db_path = Path("dbs/open_ire.db")
        run_at = datetime(2026, 2, 18)

        result = pipeline._build_db_sharepoint_path(db_path, run_at)

        assert result == "dbs/open_ire__2026-02-18.db.gz"

    def test_create_snapshot_compacts_and_compresses(
        self, pipeline: SharePointPipeline, tmp_path: Path
    ) -> None:
        """A snapshot should be gzipped and preserve the database contents."""
        db_path = _make_database(tmp_path / "open_ire.db", rows=500)
        staging = tmp_path / "staging"
        staging.mkdir()

        snapshot = pipeline._create_snapshot(db_path, staging)

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

    def test_create_snapshot_drops_free_pages(
        self, pipeline: SharePointPipeline, tmp_path: Path
    ) -> None:
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

        snapshot = pipeline._create_snapshot(db_path, staging)

        assert snapshot.stat().st_size < db_path.stat().st_size

    def test_create_snapshot_leaves_source_untouched(
        self, pipeline: SharePointPipeline, tmp_path: Path
    ) -> None:
        """The live database must not be modified by taking a snapshot."""
        db_path = _make_database(tmp_path / "open_ire.db", rows=100)
        before = db_path.read_bytes()
        staging = tmp_path / "staging"
        staging.mkdir()

        pipeline._create_snapshot(db_path, staging)

        assert db_path.read_bytes() == before

    @pytest.mark.asyncio
    async def test_upload_database_backup(
        self, pipeline: SharePointPipeline, tmp_path: Path
    ) -> None:
        db_path = _make_database(tmp_path / "open_ire.db", rows=10)
        pipeline.db_path = db_path

        backup_path = "open_ire__2026-01-27.db.gz"
        mock_upload_result = MagicMock()
        mock_upload_result.location = "https://sharepoint.com/uploaded-db"
        mock_drive_item = MagicMock()
        mock_drive_item.size = db_path.stat().st_size
        mock_drive_item.web_url = "https://sharepoint.com/db-web-url"

        uploaded_bytes: list[bytes] = []

        async def capture_upload(local_path: Path, _remote_path: str) -> MagicMock:
            uploaded_bytes.append(local_path.read_bytes())
            return mock_upload_result

        sharepoint = cast(Any, pipeline.sharepoint)
        sharepoint.upload_file = AsyncMock(side_effect=capture_upload)
        sharepoint.get_item = AsyncMock(return_value=mock_drive_item)

        with patch.object(
            pipeline,
            "_build_db_sharepoint_path",
            return_value=backup_path,
        ):
            await pipeline._upload_database_backup()

        sharepoint.upload_file.assert_awaited_once()
        snapshot_path, remote_path = sharepoint.upload_file.await_args.args
        assert remote_path == backup_path
        assert snapshot_path.name == "open_ire.db.gz"
        assert uploaded_bytes[0][:2] == b"\x1f\x8b", "snapshot should be gzipped"
        assert not snapshot_path.exists(), "staging directory should be cleaned up"
        sharepoint.get_item.assert_awaited_once_with(backup_path)

    @pytest.mark.asyncio
    async def test_upload_database_backup_snapshot_failure(
        self, pipeline: SharePointPipeline, tmp_path: Path
    ) -> None:
        """A corrupt database should be reported without attempting an upload."""
        db_path = tmp_path / "open_ire.db"
        db_path.write_text("not-a-database")
        pipeline.db_path = db_path

        sharepoint = cast(Any, pipeline.sharepoint)
        sharepoint.upload_file = AsyncMock()

        await pipeline._upload_database_backup()

        sharepoint.upload_file.assert_not_awaited()
