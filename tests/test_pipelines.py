from collections.abc import Generator
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from scrapy import Spider
from scrapy.exceptions import DropItem
from sqlmodel import SQLModel, Session, select

from open_ire.items import ArticleItem
from open_ire.models import Article
from open_ire.pipelines import (
    DuplicatesPipeline,
    SQLModelPipeline,
    SharePointPipeline,
)


@pytest.fixture
def item() -> ArticleItem:
    """Create a valid test item."""
    return ArticleItem(
        title="A Test Article",
        authors="Test Author",
        publication_date=date(2025, 6, 24),
        repository="test_repo",
        reference="TEST0001",
        url="https://example.com/article/001",
        file_urls=["https://example.com/article/001.pdf"],
        files=[
            {
                "url": "https://example.com/article/001.pdf",
                "path": "full/path/to/file.pdf",
                "checksum": "abcde12345",
            }
        ],
    )


@pytest.fixture
def spider() -> Spider:
    """Create a mock spider for testing."""
    mock_spider = MagicMock(spec=Spider)
    mock_spider.name = "test_spider"
    mock_spider.logger.warning = MagicMock()
    mock_spider.logger.error = MagicMock()

    return mock_spider


class TestDuplicatesPipeline:
    @pytest.fixture
    def pipeline(self):
        """Create a pipeline instance for testing."""
        return DuplicatesPipeline()

    def test_process_unique_item(self, pipeline, spider, item):
        """Test processing a unique item."""
        result = pipeline.process_item(item, spider)

        assert result == item
        assert item.reference in pipeline.seen

    def test_process_duplicate_item(self, pipeline, spider, item):
        """Test processing a duplicate item."""
        pipeline.seen.add(item.reference)
        with pytest.raises(DropItem) as e:
            pipeline.process_item(item, spider)

        assert item.reference in str(e.value)
        assert spider.name in str(e.value)


class TestSQLModelPipeline:
    """Tests the processing and validation logic of the SQLModelPipeline."""

    @pytest.fixture
    def pipeline(self) -> Generator[SQLModelPipeline]:
        """
        Create a pipeline instance with an in-memory SQLite DB for each test.
        """
        instance = SQLModelPipeline(":memory:", "output")
        SQLModel.metadata.create_all(instance.engine)
        yield instance
        instance.engine.dispose()

    def test_process_valid_item(
        self, pipeline: SQLModelPipeline, spider: Spider, item: ArticleItem
    ):
        """A valid item is processed successfully."""
        result = pipeline.process_item(item, spider)
        assert result is item

        with Session(pipeline.engine) as session:
            results = session.exec(select(Article)).all()
            assert len(results) == 1


class TestSharePointPipeline:
    """Tests the SharePoint pipeline for file uploads."""

    @pytest.fixture
    def pipeline(self, tmp_path):
        sharepoint_base_path = "test_sharepoint"
        local_base_path = str(tmp_path)

        with patch("open_ire.pipelines.SharePoint") as mock_sharepoint_class:
            mock_sharepoint = MagicMock()
            mock_sharepoint_class.return_value = mock_sharepoint

            pipeline = SharePointPipeline(sharepoint_base_path, local_base_path)
            pipeline.sharepoint = mock_sharepoint

            return pipeline

    @pytest.mark.asyncio
    async def test_item_with_files(self, pipeline, spider, item, tmp_path):
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
        pipeline.sharepoint.upload_file = AsyncMock(return_value=mock_upload_result)

        mock_drive_item = MagicMock()
        mock_drive_item.web_url = "https://sharepoint.com/web-url"
        pipeline.sharepoint.get_item = AsyncMock(return_value=mock_drive_item)

        result = await pipeline.process_item(item, spider)

        assert result == item
        assert result.store_urls == [
            "https://sharepoint.com/web-url",
            "https://sharepoint.com/web-url",
        ]
        assert pipeline.sharepoint.upload_file.call_count == 2

    @pytest.mark.asyncio
    async def test_process_item_no_files(self, pipeline, spider, item):
        """An item without files should have an empty store_urls list."""
        item.files = []

        result = await pipeline.process_item(item, spider)

        assert result == item
        assert result.store_urls == []
        spider.logger.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_item_upload_error(self, pipeline, spider, item):
        """Upload errors should be logged."""
        pipeline.sharepoint.upload_file = AsyncMock(
            side_effect=Exception("Upload failed")
        )

        result = await pipeline.process_item(item, spider)

        assert result == item
        assert result.store_urls == [""]
        spider.logger.error.assert_called()
