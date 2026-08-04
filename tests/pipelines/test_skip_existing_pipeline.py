from collections.abc import Generator
from typing import Any
from unittest.mock import MagicMock

import pytest
from scrapy.crawler import Crawler
from scrapy.exceptions import DropItem
from sqlmodel import Session

from open_ire.items import ArticleItem
from open_ire.models import Article, ArticleFile
from open_ire.pipelines import SkipExistingPipeline


class TestSkipExistingPipeline:
    """Tests the SkipExistingPipeline for skipping existing articles."""

    @pytest.fixture
    def pipeline_enabled(self, crawler: Crawler) -> Generator[SkipExistingPipeline, None, None]:
        crawler.settings.set("OPEN_IRE_SKIP_EXISTING", True)

        instance = SkipExistingPipeline(":memory:", "output")
        instance.crawler = crawler
        instance.open_spider()
        assert instance.engine is not None
        yield instance
        instance.engine.dispose()

    @pytest.fixture
    def pipeline_disabled(self, crawler: Crawler) -> Generator[SkipExistingPipeline, None, None]:
        crawler.settings.set("OPEN_IRE_SKIP_EXISTING", False)

        instance = SkipExistingPipeline(":memory:", "output")
        instance.crawler = crawler
        try:
            instance.open_spider()
            assert instance.engine is None
            yield instance
        finally:
            instance.close_spider()

    def test_process_item_with_skip_existing_disabled(
        self, pipeline_disabled: SkipExistingPipeline, item: ArticleItem
    ) -> None:
        result = pipeline_disabled.process_item(item)

        assert result is item

    def test_passes_through_non_article_items(
        self, pipeline_enabled: SkipExistingPipeline, item: Any
    ) -> None:
        """Test that non-ArticleItem items are passed through unchanged."""
        item = MagicMock(spec=Any)
        result = pipeline_enabled.process_item(item)

        assert result is item

    def test_process_item_with_new_article(
        self, pipeline_enabled: SkipExistingPipeline, item: ArticleItem
    ) -> None:
        result = pipeline_enabled.process_item(item)

        assert result is item

    def test_process_item_with_existing_article(
        self, pipeline_enabled: SkipExistingPipeline, item: ArticleItem
    ) -> None:
        assert pipeline_enabled.engine is not None
        with Session(pipeline_enabled.engine) as session:
            article = Article(
                title=item.title,
                authors=item.authors,
                publication_date=item.publication_date,
                repository=item.repository,
                reference=item.reference,
                url=item.url,
            )
            session.add(article)
            session.commit()

        with pytest.raises(DropItem):
            pipeline_enabled.process_item(item)

    @staticmethod
    def _save_article(pipeline: SkipExistingPipeline, item: ArticleItem) -> Article:
        assert pipeline.engine is not None
        with Session(pipeline.engine) as session:
            article = Article(
                title=item.title,
                authors=item.authors,
                publication_date=item.publication_date,
                repository=item.repository,
                reference=item.reference,
                url=item.url,
            )
            session.add(article)
            session.commit()
            session.refresh(article)

        return article

    @staticmethod
    def _use_with_files_mode(pipeline: SkipExistingPipeline) -> None:
        assert pipeline.crawler is not None
        pipeline.crawler.settings.set("OPEN_IRE_SKIP_EXISTING", False)
        pipeline.crawler.settings.set("OPEN_IRE_SKIP_EXISTING_WITH_FILES", True)

    def test_with_files_passes_existing_article_without_files(
        self, pipeline_enabled: SkipExistingPipeline, item: ArticleItem
    ) -> None:
        """In with-files mode, a known article whose file download failed is re-processed."""
        self._use_with_files_mode(pipeline_enabled)
        self._save_article(pipeline_enabled, item)

        result = pipeline_enabled.process_item(item)

        assert result is item

    def test_with_files_skips_existing_article_with_files(
        self, pipeline_enabled: SkipExistingPipeline, item: ArticleItem
    ) -> None:
        self._use_with_files_mode(pipeline_enabled)
        article = self._save_article(pipeline_enabled, item)

        assert pipeline_enabled.engine is not None
        with Session(pipeline_enabled.engine) as session:
            session.add(
                ArticleFile(
                    article_id=article.id,
                    url="https://example.com/article/001.pdf",
                    path="test_repo/001.pdf",
                    checksum="abcde12345",
                )
            )
            session.commit()

        with pytest.raises(DropItem):
            pipeline_enabled.process_item(item)
