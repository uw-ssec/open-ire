from datetime import date
from typing import cast
from unittest.mock import MagicMock

import pytest
from scrapy import Spider
from scrapy.crawler import Crawler
from scrapy.settings import Settings

from open_ire.items import ArticleItem


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
        file_reference_urls=[("https://example.com/article/002", "https://example.com/data.csv")],
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
    mock_crawler = MagicMock(spec=Crawler)
    mock_crawler.settings = Settings({"OPEN_IRE_SKIP_EXISTING": True})

    mock_spider = MagicMock(spec=Spider)
    mock_spider.name = "test_spider"
    mock_spider.logger.warning = MagicMock()
    mock_spider.logger.error = MagicMock()
    mock_spider.crawler = mock_crawler
    mock_crawler.spider = mock_spider

    return cast(Spider, mock_spider)
