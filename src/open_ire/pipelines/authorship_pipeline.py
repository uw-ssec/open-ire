import logging
from datetime import datetime
from typing import Any

from sqlmodel import Session

from open_ire.author import ParsedAuthor
from open_ire.items import ArticleItem
from open_ire.models import Article, Author, Authorship
from open_ire.pipelines.author_identifier_pipeline import find_author_by_name
from open_ire.pipelines.base_sql_model_pipeline import BaseSQLModelPipeline

logger: logging.Logger = logging.getLogger(__name__)


class AuthorshipPipeline(BaseSQLModelPipeline):
    """Link author records to article records."""

    def process_item(self, item: Any) -> Any:
        if not isinstance(item, ArticleItem):
            return item

        with Session(self.engine) as session:
            existing_article = self.find_existing_article(session, item)
            if not existing_article:
                msg = f"Article '{item.title}' not found in database"
                raise RuntimeError(msg)

            if not item.authors:
                logger.warning("No authors found for article '%s'", item.title)
                return item
            authors = ParsedAuthor.parse_author_string(item.authors or "")

            for i, author in enumerate(authors):
                existing_author = find_author_by_name(session, author)
                self._create_or_update_link(session, existing_article, existing_author, i)

            session.commit()

        return item

    @staticmethod
    def _create_or_update_link(
        session: Session,
        article: Article,
        author: Author | None,
        author_order: int,
    ) -> None:
        """Create or update an article-author record."""
        if author is None:
            return

        link = session.get(Authorship, (article.id, author.id))
        if link is None:
            logger.debug(
                "Linking article '%s' to author '%s'",
                article.id,
                author.canonical_name,
            )
            session.add(Authorship(article=article, author=author, author_order=author_order))
            return

        if link.author_order != author_order:
            logger.debug(
                "Updating author_order for article '%s' and author '%s' from %s to %s",
                article.id,
                author.canonical_name,
                link.author_order,
                author_order,
            )
            link.author_order = author_order
            link.updated_at = datetime.now()
