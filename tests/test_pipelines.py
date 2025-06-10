from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from scrapy import Spider
from scrapy.exceptions import DropItem

from open_ire.items import OpenIreItem
from open_ire.pipelines import DuplicatesPipeline


class TestDuplicatesPipeline:
    @pytest.fixture
    def pipeline(self):
        """Create a pipeline instance for testing."""
        return DuplicatesPipeline()

    @pytest.fixture
    def spider(self):
        """Create a mock spider for testing."""
        spider = MagicMock(spec=Spider)
        spider.name = "test_spider"
        return spider

    @pytest.fixture
    def item(self):
        """Create a test item."""
        return OpenIreItem(
            authors="Test Author",
            file_urls=["https://example.com/test.pdf"],
            publication_date="2023",
            reference="TEST123",
            repository="test",
            title="Test Title",
            url="https://example.com/test",
        )

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
