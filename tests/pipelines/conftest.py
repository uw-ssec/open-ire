from datetime import date
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest
from scrapy import Spider
from scrapy.crawler import Crawler
from scrapy.settings import Settings

from open_ire.db import create_db_engine
from open_ire.enums import RunKind
from open_ire.items import ArticleItem
from open_ire.provenance import begin_run


@pytest.fixture
def temp_db(tmp_path: Path) -> str:
    """SQLite database URI for testing, with a run open as during a crawl.

    The database's audit triggers reject writes unless a run is open.
    """
    db_path = tmp_path / "test.db"
    engine = create_db_engine(str(db_path))
    with engine.begin() as connection:
        begin_run(connection, RunKind.CRAWL, spider="test_spider")
    engine.dispose()
    return f"sqlite:///{db_path}"


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
def crawler() -> Crawler:
    """Create a mock crawler for testing."""
    mock_crawler = MagicMock(spec=Crawler)

    mock_spider = MagicMock(spec=Spider)
    mock_spider.name = "test_spider"
    mock_spider.logger.warning = MagicMock()
    mock_spider.logger.error = MagicMock()
    mock_spider.crawler = mock_crawler

    mock_crawler.spider = mock_spider
    mock_crawler.settings = Settings()

    return cast(Crawler, mock_crawler)
