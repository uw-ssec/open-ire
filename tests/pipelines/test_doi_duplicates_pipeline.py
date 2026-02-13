from collections.abc import Generator
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from scrapy.crawler import Crawler
from scrapy.exceptions import DropItem
from sqlmodel import Session, select

from open_ire.items import ArticleItem
from open_ire.models import Article
from open_ire.pipelines import DOIDuplicatesPipeline


@pytest.fixture
def pipeline(crawler: Crawler) -> Generator[DOIDuplicatesPipeline, None, None]:
    instance = DOIDuplicatesPipeline(":memory:", "output")
    instance.crawler = crawler
    instance.open_spider()
    assert instance.engine is not None
    yield instance
    instance.engine.dispose()


@pytest.fixture
def pipeline_with_existing(
    crawler: Crawler,
    tmp_path: Path,
) -> Generator[DOIDuplicatesPipeline, None, None]:
    """Pipeline with an existing OpenAlex article in the database."""
    db_path = str(tmp_path / "test.db")
    instance = DOIDuplicatesPipeline(db_path, "output")
    instance.crawler = crawler

    # First open: create the schema and seed an article.
    instance.open_spider()
    assert instance.engine is not None
    with Session(instance.engine) as session:
        article = Article(
            title="Existing Article",
            authors="Author One",
            publication_date=date(2024, 1, 15),
            repository="openalex",
            reference="OA123",
            url="https://doi.org/10.1234/test",
            doi="10.1234/test",
        )
        session.add(article)
        session.commit()
    instance.close_spider()

    # Second open: reload the DOI cache from the persisted DB.
    instance.open_spider()
    assert instance.engine is not None
    yield instance
    instance.close_spider()


class TestDOIDuplicatesPipeline:
    def test_passes_through_non_article_items(self, pipeline: DOIDuplicatesPipeline) -> None:
        item = MagicMock()
        result = pipeline.process_item(item)
        assert result is item

    def test_passes_through_article_without_doi(
        self, pipeline: DOIDuplicatesPipeline, item: ArticleItem
    ) -> None:
        item.doi = None
        result = pipeline.process_item(item)
        assert result is item
        assert "duplicate_sources" not in item.extra

    def test_passes_through_article_with_no_existing_match(
        self, pipeline: DOIDuplicatesPipeline, item: ArticleItem
    ) -> None:
        item.doi = "10.9999/unique"
        result = pipeline.process_item(item)
        assert result is item
        assert "duplicate_sources" not in item.extra

    def test_drops_duplicate_doi_and_stores_duplicate_source(
        self,
        pipeline_with_existing: DOIDuplicatesPipeline,
    ) -> None:
        pipeline = pipeline_with_existing
        assert pipeline.engine is not None
        wos_item = ArticleItem(
            title="Same Article from WoS",
            authors="Author One",
            publication_date=date(2024, 1, 15),
            repository="wos",
            reference="WOS:000123",
            url="https://www.webofscience.com/wos/woscc/full-record/WOS:000123",
            doi="10.1234/test",
        )

        with pytest.raises(DropItem):
            pipeline.process_item(wos_item)

        with Session(pipeline.engine) as session:
            article = session.exec(select(Article).where(Article.doi == "10.1234/test")).first()
            assert article is not None
            duplicate_sources = article.extra["duplicate_sources"]
            assert duplicate_sources == [
                {"repository": "wos", "reference": "WOS:000123", "title": "Same Article from WoS"}
            ]

    def test_normalizes_doi_before_matching(
        self,
        pipeline_with_existing: DOIDuplicatesPipeline,
    ) -> None:
        """DOI with URL prefix should still merge against normalized DOI in the database."""
        pipeline = pipeline_with_existing
        assert pipeline.engine is not None
        wos_item = ArticleItem(
            title="Article with URL DOI",
            authors="Author One",
            publication_date=date(2024, 1, 15),
            repository="wos",
            reference="WOS:000456",
            url="https://www.webofscience.com/wos/woscc/full-record/WOS:000456",
            doi="https://doi.org/10.1234/test",
        )

        with pytest.raises(DropItem):
            pipeline.process_item(wos_item)

        with Session(pipeline.engine) as session:
            article = session.exec(select(Article).where(Article.doi == "10.1234/test")).first()
            assert article is not None
            duplicate_sources = article.extra["duplicate_sources"]
            assert duplicate_sources == [
                {"repository": "wos", "reference": "WOS:000456", "title": "Article with URL DOI"}
            ]
