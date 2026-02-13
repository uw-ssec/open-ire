import logging
from dataclasses import dataclass
from typing import Any

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


class CrossSourceDeduplicationPipeline(BaseSQLModelPipeline):
    """Annotates articles whose DOI already exists in the database from a different source.

    When an article is found with a matching DOI from another repository, the item's
    ``extra`` dict is updated with ``cross_source_doi_match`` containing the UUID and
    repository of the existing record.
    """

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

        existing = self._doi_to_article.get(doi)
        if existing is not None and existing.repository != item.repository:
            item.extra["cross_source_doi_match"] = {
                "article_id": existing.id,
                "repository": existing.repository,
                "reference": existing.reference,
            }
            logger.info(
                "Article '%s' (DOI: %s) from '%s' also exists in '%s' (id: %s).",
                item.reference,
                doi,
                item.repository,
                existing.repository,
                existing.id,
            )

        # Track this DOI for in-session dedup across spiders (if ever run together).
        # In-session items don't have a DB ID yet, so store repository info only.
        self._doi_to_article.setdefault(
            doi, _ExistingArticle(id="", repository=item.repository, reference=item.reference)
        )

        return item
