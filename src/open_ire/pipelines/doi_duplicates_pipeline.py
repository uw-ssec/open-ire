import logging
from dataclasses import dataclass
from typing import Any

from scrapy.exceptions import DropItem
from sqlmodel import Session, select

from open_ire.items import ArticleItem
from open_ire.models import Article
from open_ire.pipelines.base_sql_model_pipeline import BaseSQLModelPipeline
from open_ire.pipelines.doi_normalization_pipeline import DOINormalizationPipeline

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _ExistingArticle:
    id: str
    repository: str
    reference: str


class DOIDuplicatesPipeline(BaseSQLModelPipeline):
    """Merges duplicate DOI records into an existing article and drops the duplicate item."""

    def __init__(self, db_path: str, files_base_path: str | None = None) -> None:
        super().__init__(db_path, files_base_path)
        # Populated once spider opens
        self._doi_to_article: dict[str, _ExistingArticle] = {}

    def open_spider(self) -> None:
        super().open_spider()

        assert self.engine is not None
        with Session(self.engine) as session:
            rows = session.exec(
                select(Article.id, Article.doi, Article.repository, Article.reference).where(
                    Article.doi.is_not(None)  # type: ignore[union-attr]
                )
            ).all()
            for article_id, doi, repository, reference in rows:
                assert doi is not None
                self._doi_to_article[doi] = _ExistingArticle(
                    id=str(article_id), repository=repository, reference=reference
                )

    def process_item(self, item: Any) -> Any:
        if not isinstance(item, ArticleItem):
            return item

        doi = DOINormalizationPipeline.normalize(item.doi)
        if doi is None:
            return item

        if doi not in self._doi_to_article:
            return item

        assert self.engine is not None
        with Session(self.engine) as session:
            existing_article = session.exec(select(Article).where(Article.doi == doi)).first()
            if existing_article is None:
                return item
            self._append_duplicate_source(existing_article, item)
            session.add(existing_article)
            session.commit()

        cached = self._doi_to_article[doi]
        logger.info(
            "Dropping article '%s' from '%s': DOI '%s' already exists as '%s' in '%s'.",
            item.reference,
            item.repository,
            doi,
            cached.reference,
            cached.repository,
        )
        msg = "Article DOI already exists in database."
        raise DropItem(msg)

    @staticmethod
    def _append_duplicate_source(existing_article: Article, item: ArticleItem) -> None:
        """Add the duplicate source reference to the existing article's extra data."""
        new_source: dict[str, Any] = {
            "repository": item.repository,
            "reference": item.reference,
        }
        new_source |= DOIDuplicatesPipeline._differing_fields(existing_article, item)

        extra = dict(existing_article.extra)
        duplicate_sources = extra.get("duplicate_sources", [])
        if not isinstance(duplicate_sources, list):
            duplicate_sources = []
        if new_source not in duplicate_sources:
            duplicate_sources.append(new_source)
        extra["duplicate_sources"] = duplicate_sources
        existing_article.extra = extra

    @staticmethod
    def _differing_fields(existing_article: Article, item: ArticleItem) -> dict[str, Any]:
        """Returns a dictionary of fields from the duplicate item that differ from the existing article."""
        diffs: dict[str, Any] = {}
        if item.title != existing_article.title:
            diffs["title"] = item.title
        if item.publication_date != existing_article.publication_date:
            diffs["publication_date"] = str(item.publication_date)
        if item.type != existing_article.type:
            diffs["type"] = item.type.value if item.type else None
        return diffs
