from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace

import pytest
from scrapy import Spider
from sqlmodel import Session, select

from open_ire.items import ArticleItem
from open_ire.models import Article, ArticleFile, ArticleFileReference
from open_ire.pipelines import SQLModelPipeline


class TestSQLModelPipeline:
    """Tests the processing and validation logic of the SQLModelPipeline."""

    @pytest.fixture
    def pipeline(self, spider: Spider) -> Generator[SQLModelPipeline, None, None]:
        """
        Create a pipeline instance with an in-memory SQLite DB for each test.
        """
        instance = SQLModelPipeline(":memory:", "output")
        instance.crawler = spider.crawler
        instance.open_spider()
        assert instance.engine is not None
        yield instance
        instance.engine.dispose()

    def test_process_valid_item(
        self, pipeline: SQLModelPipeline, spider: Spider, item: ArticleItem
    ) -> None:
        """A valid item is processed successfully."""
        result = pipeline.process_item(item)
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
    ) -> None:
        """Test updating an existing article."""

        pipeline.process_item(item)
        pipeline.process_item(item_with_file_references)

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

            file_refs = session.exec(select(ArticleFileReference)).all()
            assert len(file_refs) == 1

    def test_update_existing_article_with_new_files(
        self, pipeline: SQLModelPipeline, spider: Spider, item: ArticleItem
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

    def test_file_deduplication(
        self, pipeline: SQLModelPipeline, spider: Spider, item: ArticleItem
    ) -> None:
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

    def test_file_reference_deduplication(
        self,
        pipeline: SQLModelPipeline,
        spider: Spider,
        item_with_file_references: ArticleItem,
    ) -> None:
        """Test that file references with the same URL are not duplicated."""

        pipeline.process_item(item_with_file_references)

        item_data = item_with_file_references.model_dump()
        item_data.update(
            {
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
            }
        )
        updated_item = ArticleItem(**item_data)

        pipeline.process_item(updated_item)

        with Session(pipeline.engine) as session:
            file_refs = session.exec(select(ArticleFileReference)).all()
            assert len(file_refs) == 1

    def test_from_crawler_creates_missing_db_parent_dir(self, tmp_path: Path) -> None:
        missing_db = str(tmp_path / "missing_parent" / "open_ire.db")
        crawler = SimpleNamespace(
            settings={"OPEN_IRE_DATABASE_FILE": missing_db, "FILES_STORE": str(tmp_path)}
        )
        SQLModelPipeline.from_crawler(crawler)  # type: ignore[arg-type]
        assert Path(missing_db).parent.exists()
