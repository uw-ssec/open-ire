from typing import Any
from unittest.mock import MagicMock

import pytest
from scrapy.crawler import Crawler
from scrapy.exceptions import DropItem

from open_ire.items import ArticleItem
from open_ire.pipelines import DuplicatesPipeline


class TestDuplicatesPipeline:
    @pytest.fixture
    def pipeline(self, crawler: Crawler) -> DuplicatesPipeline:
        """Create a pipeline instance for testing."""
        pipeline = DuplicatesPipeline()
        pipeline.crawler = crawler
        pipeline.open_spider()
        assert pipeline.crawler is not None
        assert pipeline.crawler.spider is not None
        return pipeline

    def test_passes_through_non_article_items(self, pipeline: DuplicatesPipeline) -> None:
        """Test that non-ArticleItem items are passed through unchanged."""
        item = MagicMock(spec=Any)
        result = pipeline.process_item(item)

        assert result is item

    def test_process_unique_item(self, pipeline: DuplicatesPipeline, item: ArticleItem) -> None:
        """Test processing a unique item."""
        result = pipeline.process_item(item)

        assert result == item
        assert item.reference in pipeline.seen

    def test_process_duplicate_item(self, pipeline: DuplicatesPipeline, item: ArticleItem) -> None:
        """Test processing a duplicate item."""
        pipeline.seen.add(item.reference)
        with pytest.raises(DropItem) as e:
            pipeline.process_item(item)

        assert item.reference in str(e.value)
        assert pipeline.crawler is not None
        assert pipeline.crawler.spider is not None
        assert pipeline.crawler.spider.name in str(e.value)
