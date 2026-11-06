import logging
import os
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from scrapy.crawler import Crawler
from scrapy.exceptions import NotConfigured

from open_ire.items import ArticleItem
from open_ire.pipelines import SharePointPipeline
from open_ire.sharepoint import SharePointClient

SHAREPOINT_ENV = {
    "SHAREPOINT_CLIENT_ID": "test-client-id",
    "SHAREPOINT_TENANT_ID": "test-tenant-id",
    "SHAREPOINT_SITE_ID": "test-site-id",
    "SHAREPOINT_CLIENT_SECRET": "test-client-secret",
}


class TestSharePointPipeline:
    """Tests the SharePoint pipeline for file uploads."""

    @pytest.fixture
    def pipeline(self, tmp_path: Path) -> SharePointPipeline:
        with patch("open_ire.pipelines.sharepoint_pipeline.SharePointClient"):
            return SharePointPipeline("test_sharepoint", str(tmp_path))

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
        assert sharepoint.upload_file.await_args_list == [
            call(file1, "file1.pdf"),
            call(file2, "file2.pdf"),
        ]

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

    @pytest.mark.asyncio
    async def test_close_spider_closes_client(self, pipeline: SharePointPipeline) -> None:
        sharepoint = cast(Any, pipeline.sharepoint)
        sharepoint.close = AsyncMock()

        await pipeline.close_spider()

        sharepoint.close.assert_awaited_once_with()

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
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        env: dict[str, str],
        missing: list[str],
    ) -> None:
        crawler.settings.set("FILES_STORE", str(tmp_path))
        crawler.signals = MagicMock()

        with (
            patch.dict(os.environ, env, clear=True),
            patch(
                "open_ire.pipelines.sharepoint_pipeline.SharePointClient", wraps=SharePointClient
            ) as cls,
            caplog.at_level(logging.WARNING),
            pytest.raises(NotConfigured),
        ):
            SharePointPipeline.from_crawler(crawler)

        cls.assert_not_called()
        cast(Any, crawler.signals.connect).assert_not_called()

        assert len(caplog.records) == 1
        message = caplog.records[0].getMessage()
        assert all(name in message for name in missing)
        assert all(value not in message for value in env.values() if value)

    def test_from_crawler(self, crawler: Crawler, tmp_path: Path) -> None:
        crawler.settings.set("FILES_STORE", str(tmp_path))
        crawler.settings.set("OPEN_IRE_SHAREPOINT_BASE_PATH", "test_sharepoint")

        with (
            patch.dict(os.environ, SHAREPOINT_ENV, clear=True),
            patch(
                "open_ire.pipelines.sharepoint_pipeline.SharePointClient", wraps=SharePointClient
            ) as cls,
            patch.object(SharePointClient, "_create_client"),
        ):
            pipeline = SharePointPipeline.from_crawler(crawler)

        cls.assert_called_once_with(base_path="test_sharepoint")
        assert pipeline.files_store == tmp_path
