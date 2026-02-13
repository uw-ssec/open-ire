from collections.abc import Generator
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from scrapy.crawler import Crawler
from sqlmodel import Session

from open_ire.items import ArticleItem
from open_ire.models import Article
from open_ire.pipelines import CrossSourceDeduplicationPipeline


@pytest.fixture
def pipeline(crawler: Crawler) -> Generator[CrossSourceDeduplicationPipeline, None, None]:
    instance = CrossSourceDeduplicationPipeline(":memory:", "output")
    instance.crawler = crawler
    instance.open_spider()
    assert instance.engine is not None
    yield instance
    instance.engine.dispose()


@pytest.fixture
def pipeline_with_existing(
    crawler: Crawler,
    tmp_path: Path,
) -> Generator[tuple[CrossSourceDeduplicationPipeline, str], None, None]:
    """Pipeline with an existing OpenAlex article in the database.

    Yields a (pipeline, article_id) tuple so tests can assert on the UUID.
    """
    db_path = str(tmp_path / "test.db")
    instance = CrossSourceDeduplicationPipeline(db_path, "output")
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
        session.refresh(article)
        article_id = str(article.id)
    instance.close_spider()

    # Second open: reload the DOI cache from the persisted DB.
    instance.open_spider()
    yield instance, article_id
    instance.close_spider()


class TestCrossSourceDeduplicationPipeline:
    def test_passes_through_non_article_items(
        self, pipeline: CrossSourceDeduplicationPipeline
    ) -> None:
        item = MagicMock()
        result = pipeline.process_item(item)
        assert result is item

    def test_passes_through_article_without_doi(
        self, pipeline: CrossSourceDeduplicationPipeline, item: ArticleItem
    ) -> None:
        item.doi = None
        result = pipeline.process_item(item)
        assert result is item
        assert "cross_source_doi_match" not in item.extra

    def test_passes_through_article_with_no_existing_match(
        self, pipeline: CrossSourceDeduplicationPipeline, item: ArticleItem
    ) -> None:
        item.doi = "10.9999/unique"
        result = pipeline.process_item(item)
        assert result is item
        assert "cross_source_doi_match" not in item.extra

    def test_annotates_cross_source_duplicate(
        self,
        pipeline_with_existing: tuple[CrossSourceDeduplicationPipeline, str],
    ) -> None:
        pipeline, article_id = pipeline_with_existing
        wos_item = ArticleItem(
            title="Same Article from WoS",
            authors="Author One",
            publication_date=date(2024, 1, 15),
            repository="wos",
            reference="WOS:000123",
            url="https://www.webofscience.com/wos/woscc/full-record/WOS:000123",
            doi="10.1234/test",
        )

        result = pipeline.process_item(wos_item)

        assert result is wos_item
        match = wos_item.extra["cross_source_doi_match"]
        assert match["article_id"] == article_id
        assert match["repository"] == "openalex"

    def test_does_not_annotate_same_source(
        self,
        pipeline_with_existing: tuple[CrossSourceDeduplicationPipeline, str],
    ) -> None:
        pipeline, _ = pipeline_with_existing
        openalex_item = ArticleItem(
            title="Another OpenAlex Article",
            authors="Author Two",
            publication_date=date(2024, 2, 1),
            repository="openalex",
            reference="OA456",
            url="https://doi.org/10.1234/test",
            doi="10.1234/test",
        )

        result = pipeline.process_item(openalex_item)

        assert result is openalex_item
        assert "cross_source_doi_match" not in openalex_item.extra

    def test_tracks_dois_within_session(self, pipeline: CrossSourceDeduplicationPipeline) -> None:
        """First item from openalex is tracked; second from wos is annotated."""
        first = ArticleItem(
            title="First",
            authors="Author",
            publication_date=date(2024, 1, 1),
            repository="openalex",
            reference="OA001",
            url="https://doi.org/10.5555/session",
            doi="10.5555/session",
        )
        second = ArticleItem(
            title="Second",
            authors="Author",
            publication_date=date(2024, 1, 1),
            repository="wos",
            reference="WOS:001",
            url="https://www.webofscience.com/wos/woscc/full-record/WOS:001",
            doi="10.5555/session",
        )

        pipeline.process_item(first)
        pipeline.process_item(second)

        assert "cross_source_doi_match" not in first.extra
        match = second.extra["cross_source_doi_match"]
        assert match["repository"] == "openalex"
        assert match["article_id"] == ""  # no DB ID for in-session items

    def test_normalizes_doi_before_matching(
        self,
        pipeline_with_existing: tuple[CrossSourceDeduplicationPipeline, str],
    ) -> None:
        """DOI with URL prefix should still match normalized DOI in the database."""
        pipeline, article_id = pipeline_with_existing
        wos_item = ArticleItem(
            title="Article with URL DOI",
            authors="Author One",
            publication_date=date(2024, 1, 15),
            repository="wos",
            reference="WOS:000456",
            url="https://www.webofscience.com/wos/woscc/full-record/WOS:000456",
            doi="https://doi.org/10.1234/test",
        )

        result = pipeline.process_item(wos_item)

        assert result is wos_item
        match = wos_item.extra["cross_source_doi_match"]
        assert match["article_id"] == article_id
        assert match["repository"] == "openalex"
