import pytest
from scrapy import Spider
from scrapy.exceptions import DropItem

from open_ire.items import ArticleItem
from open_ire.pipelines import DuplicatesPipeline


class TestDuplicatesPipeline:
    @pytest.fixture
    def pipeline(self, spider: Spider) -> DuplicatesPipeline:
        """Create a pipeline instance for testing."""
        pipeline = DuplicatesPipeline()
        pipeline.crawler = spider.crawler
        pipeline.open_spider()
        return pipeline

    def test_process_unique_item(
        self, pipeline: DuplicatesPipeline, spider: Spider, item: ArticleItem
    ) -> None:
        """Test processing a unique item."""
        result = pipeline.process_item(item)

        assert result == item
        assert item.reference in pipeline.seen

    def test_process_duplicate_item(
        self, pipeline: DuplicatesPipeline, spider: Spider, item: ArticleItem
    ) -> None:
        """Test processing a duplicate item."""
        pipeline.seen.add(item.reference)
        with pytest.raises(DropItem) as e:
            pipeline.process_item(item)

        assert item.reference in str(e.value)
        assert spider.name in str(e.value)
