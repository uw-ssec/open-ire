from collections.abc import Generator
from datetime import date
from unittest.mock import MagicMock

import pytest
from scrapy import Spider
from scrapy.exceptions import DropItem
from sqlmodel import SQLModel, Session, select

from open_ire.items import ArticleItem
from open_ire.models import Article
from open_ire.pipelines import (
    DuplicatesPipeline,
    SQLModelPipeline,
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
        instance = SQLModelPipeline(":memory:")
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

    def test_drops_item_on_empty_files(
        self, pipeline: SQLModelPipeline, spider: Spider, item: ArticleItem
    ):
        """An item is dropped if its 'files' attribute is empty."""
        item.files = []
        with pytest.raises(DropItem):
            pipeline.process_item(item, spider)

    def test_drops_item_on_invalid_files(
        self, pipeline: SQLModelPipeline, spider: Spider, item: ArticleItem
    ):
        """An item is dropped if all its files fail validation."""
        item.files = [{"url": "https://example.com/file.pdf"}]
        with pytest.raises(DropItem):
            pipeline.process_item(item, spider)

    def test_drops_duplicate_item(
        self, pipeline: SQLModelPipeline, spider: Spider, item: ArticleItem
    ):
        """The pipeline drops an item that violates the unique constraint."""
        pipeline.process_item(item, spider)

        duplicate_item = item.model_copy()
        duplicate_item.title = "A Different Title, Same Reference"

        with pytest.raises(DropItem):
            pipeline.process_item(duplicate_item, spider)
