from collections.abc import Generator

import pytest
from scrapy import Spider
from scrapy.exceptions import DropItem
from sqlmodel import Session

from open_ire.items import ArticleItem
from open_ire.models import Article
from open_ire.pipelines import SkipExistingPipeline


class TestSkipExistingPipeline:
    """Tests the SkipExistingPipeline for skipping existing articles."""

    @pytest.fixture
    def pipeline_enabled(self, spider: Spider) -> Generator[SkipExistingPipeline, None, None]:
        spider.crawler.settings.set("OPEN_IRE_SKIP_EXISTING", True)

        instance = SkipExistingPipeline(":memory:", "output")
        instance.crawler = spider.crawler
        instance.open_spider()
        assert instance.engine is not None
        yield instance
        instance.engine.dispose()

    @pytest.fixture
    def pipeline_disabled(self, spider: Spider) -> SkipExistingPipeline:
        spider.crawler.settings.set("OPEN_IRE_SKIP_EXISTING", False)

        instance = SkipExistingPipeline(":memory:", "output")
        instance.crawler = spider.crawler
        instance.open_spider()
        assert instance.engine is None
        return instance

    def test_process_item_with_skip_existing_disabled(
        self, pipeline_disabled: SkipExistingPipeline, spider: Spider, item: ArticleItem
    ) -> None:
        result = pipeline_disabled.process_item(item)

        assert result is item

    def test_process_item_with_new_article(
        self, pipeline_enabled: SkipExistingPipeline, spider: Spider, item: ArticleItem
    ) -> None:
        result = pipeline_enabled.process_item(item)

        assert result is item

    def test_process_item_with_existing_article(
        self, pipeline_enabled: SkipExistingPipeline, spider: Spider, item: ArticleItem
    ) -> None:
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
