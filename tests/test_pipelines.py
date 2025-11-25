from collections.abc import Generator
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from scrapy import Spider
from scrapy.exceptions import DropItem
from sqlmodel import SQLModel, Session, select

from open_ire.items import ArticleItem
from open_ire.models import Article, ArticleFile, ArticleFileReference
from open_ire.pipelines import (
    DuplicatesPipeline,
    SQLModelPipeline,
    OAPPublicationSQLModelPipeline,
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
def item_with_file_references() -> ArticleItem:
    """Create a test item with files and file references."""
    return ArticleItem(
        title="Article with References",
        authors="Test Author",
        publication_date=date(2025, 6, 24),
        repository="test_repo",
        reference="TEST0002",
        url="https://example.com/article/002",
        file_reference_urls=[
            ("https://example.com/article/002", "https://example.com/data.csv")
        ],
        file_references=[
            {
                "url": "https://example.com/data.csv",
                "source_url": "https://example.com/article/002",
                "extension": "csv",
                "size": 1024,
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
    def pipeline(self, spider) -> Generator[SQLModelPipeline]:
        """
        Create a pipeline instance with an in-memory SQLite DB for each test.
        """
        instance = SQLModelPipeline(":memory:", "output")
        instance.open_spider(spider)
        assert instance.engine is not None
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

    def test_update_existing_article(
        self,
        pipeline: SQLModelPipeline,
        spider: Spider,
        item: ArticleItem,
        item_with_file_references: ArticleItem,
    ):
        """Test updating an existing article."""

        pipeline.process_item(item, spider)
        pipeline.process_item(item_with_file_references, spider)

        item_data = item.model_dump()
        item_data.update({
            "title": "Updated Article Title",
            "file_urls": [
                "https://example.com/article/001.pdf",  # Existing file
                "https://example.com/article/001-supplement.pdf",  # New file
            ],
            "files": [
                {
                    "url": "https://example.com/article/001.pdf",
                    "path": "full/path/to/file.pdf",
                    "checksum": "abcde12345",  # Same checksum
                },
                {
                    "url": "https://example.com/article/001-supplement.pdf",
                    "path": "full/path/to/supplement.pdf",
                    "checksum": "supplement123",  # New file
                },
            ],
        })
        updated_item = ArticleItem(**item_data)

        pipeline.process_item(updated_item, spider)

        with Session(pipeline.engine) as session:
            articles = session.exec(select(Article)).all()
            assert len(articles) == 2

            first_article = session.exec(
                select(Article).where(Article.reference == "TEST0001")
            ).first()
            assert first_article is not None
            assert first_article.title == "Updated Article Title"
            assert len(first_article.files) == 2
            checksums = {f.checksum for f in first_article.files}
            assert checksums == {"abcde12345", "supplement123"}

            file_refs = session.exec(select(ArticleFileReference)).all()
            assert len(file_refs) == 1

    def test_update_existing_article_with_new_files(
        self, pipeline: SQLModelPipeline, spider: Spider, item: ArticleItem
    ):
        """Test updating an existing article with new files."""

        pipeline.process_item(item, spider)

        item_data = item.model_dump()
        item_data.update({
            "file_urls": ["https://example.com/article/001-v2.pdf"],
            "files": [
                {
                    "url": "https://example.com/article/001-v2.pdf",
                    "path": "full/path/to/file-v2.pdf",
                    "checksum": "xyz789",
                }
            ],
        })
        updated_item = ArticleItem(**item_data)
        result = pipeline.process_item(updated_item, spider)

        assert result is updated_item

        with Session(pipeline.engine) as session:
            # Should still have only one article
            articles = session.exec(select(Article)).all()
            assert len(articles) == 1

            # Should have both files (original and new)
            files = session.exec(select(ArticleFile)).all()
            assert len(files) == 2
            checksums = {f.checksum for f in files}
            assert checksums == {"abcde12345", "xyz789"}

    def test_file_deduplication(
        self, pipeline: SQLModelPipeline, spider: Spider, item: ArticleItem
    ):
        """Test that files with the same URL and same checksum are not duplicated."""
        pipeline.process_item(item, spider)

        item_data = item.model_dump()
        item_data.update({
            "title": "Different Title",
            "files": [
                {
                    "url": "https://example.com/article/001.pdf",
                    "path": "different/path/to/file.pdf",
                    "checksum": "abcde12345",
                }
            ],
        })
        updated_item = ArticleItem(**item_data)
        pipeline.process_item(updated_item, spider)

        with Session(pipeline.engine) as session:
            files = session.exec(select(ArticleFile)).all()
            assert len(files) == 1  # Should not duplicate

    def test_file_reference_deduplication(
        self,
        pipeline: SQLModelPipeline,
        spider: Spider,
        item_with_file_references: ArticleItem,
    ):
        """Test that file references with the same URL are not duplicated."""

        pipeline.process_item(item_with_file_references, spider)

        item_data = item_with_file_references.model_dump()
        item_data.update({
            "title": "Updated Title",
            "file_reference_urls": [
                ("https://example.com/article/002", "https://example.com/data.csv")
            ],
            "file_references": [
                {
                    "url": "https://example.com/data.csv",
                    "source_url": "https://example.com/article/002",
                    "extension": "csv",
                    "size": 2048,
                }
            ],
        })
        updated_item = ArticleItem(**item_data)

        pipeline.process_item(updated_item, spider)

        with Session(pipeline.engine) as session:
            file_refs = session.exec(select(ArticleFileReference)).all()
            assert len(file_refs) == 1

    def test_from_crawler_creates_missing_db_parent_dir(self, tmp_path: Path):
        from types import SimpleNamespace
        missing_db = str(tmp_path / "missing_parent" / "open_ire.db")
        crawler = SimpleNamespace(settings={"OPEN_IRE_DATABASE_FILE": missing_db,
                                            "FILES_STORE": str(tmp_path)})
        pipeline = SQLModelPipeline.from_crawler(crawler)  # type: ignore[arg-type]
        assert Path(missing_db).parent.exists()

class TestOAPPublicationSQLModelPipeline:
    """Tests the processing and validation logic of the OAPPublicationSQLModelPipeline."""

    def test_from_crawler_creates_missing_db_parent_dir(self, tmp_path: Path):
        from types import SimpleNamespace
        missing_db = str(tmp_path / "missing_parent" / "open_ire.db")
        crawler = SimpleNamespace(settings={"OPEN_IRE_DATABASE_FILE": missing_db,
                                            "FILES_STORE": str(tmp_path)})
        pipeline = OAPPublicationSQLModelPipeline.from_crawler(crawler)  # type: ignore[arg-type]
        assert Path(missing_db).parent.exists()

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
