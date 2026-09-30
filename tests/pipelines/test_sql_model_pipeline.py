from collections.abc import Generator
from typing import Any
from unittest.mock import MagicMock

import pytest
from scrapy.crawler import Crawler
from sqlmodel import Session, select

from open_ire.items import ArticleItem
from open_ire.models import Article, ArticleFile
from open_ire.pipelines import SQLModelPipeline


class TestSQLModelPipeline:
    """Tests the processing and validation logic of the SQLModelPipeline."""

    @pytest.fixture
    def pipeline(self, crawler: Crawler) -> Generator[SQLModelPipeline, None, None]:
        """
        Create a pipeline instance with an in-memory SQLite DB for each test.
        """
        instance = SQLModelPipeline(":memory:", "output")
        instance.crawler = crawler
        instance.open_spider()
        assert instance.engine is not None
        yield instance
        instance.engine.dispose()

    def test_passes_through_non_article_items(self, pipeline: SQLModelPipeline) -> None:
        """Test that non-ArticleItem items are passed through unchanged."""
        item = MagicMock(spec=Any)
        result = pipeline.process_item(item)

        assert result is item

    def test_process_valid_item(self, pipeline: SQLModelPipeline, item: ArticleItem) -> None:
        """A valid item is processed successfully."""
        result = pipeline.process_item(item)
        assert result is item

        with Session(pipeline.engine) as session:
            results = session.exec(select(Article)).all()
            assert len(results) == 1

    def test_update_existing_article(
        self,
        pipeline: SQLModelPipeline,
        item: ArticleItem,
    ) -> None:
        """Test updating an existing article leaves other articles alone."""

        other_item = item.model_copy(
            update={
                "title": "Another Article",
                "reference": "TEST0002",
                "url": "https://example.com/article/002",
                "file_urls": [],
                "files": None,
            }
        )
        pipeline.process_item(item)
        pipeline.process_item(other_item)

        item_data = item.model_dump()
        item_data.update(
            {
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
            }
        )
        updated_item = ArticleItem(**item_data)

        pipeline.process_item(updated_item)

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

            second_article = session.exec(
                select(Article).where(Article.reference == "TEST0002")
            ).first()
            assert second_article is not None
            assert second_article.title == "Another Article"
            assert second_article.files == []

    def test_update_existing_article_with_new_files(
        self, pipeline: SQLModelPipeline, item: ArticleItem
    ) -> None:
        """Test updating an existing article with new files."""

        pipeline.process_item(item)

        item_data = item.model_dump()
        item_data.update(
            {
                "file_urls": ["https://example.com/article/001-v2.pdf"],
                "files": [
                    {
                        "url": "https://example.com/article/001-v2.pdf",
                        "path": "full/path/to/file-v2.pdf",
                        "checksum": "xyz789",
                    }
                ],
            }
        )
        updated_item = ArticleItem(**item_data)
        result = pipeline.process_item(updated_item)

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

    def test_file_deduplication(self, pipeline: SQLModelPipeline, item: ArticleItem) -> None:
        """Test that files with the same URL and same checksum are not duplicated."""
        pipeline.process_item(item)

        item_data = item.model_dump()
        item_data.update(
            {
                "title": "Different Title",
                "files": [
                    {
                        "url": "https://example.com/article/001.pdf",
                        "path": "different/path/to/file.pdf",
                        "checksum": "abcde12345",
                    }
                ],
            }
        )
        updated_item = ArticleItem(**item_data)
        pipeline.process_item(updated_item)

        with Session(pipeline.engine) as session:
            files = session.exec(select(ArticleFile)).all()
            assert len(files) == 1  # Should not duplicate


def test_invalid_file_metadata_is_skipped() -> None:
    item = ArticleItem(reference="test", repository="test", title="Test", url="https://example.com")
    pipeline = SQLModelPipeline(":memory:", "output")
    item.files = [{"url": "https://example.com/file", "path": "file.pdf"}]
    assert pipeline._get_article_files(item) == []
