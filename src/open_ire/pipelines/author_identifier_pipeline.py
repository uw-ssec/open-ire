import logging
from datetime import datetime
from typing import Any

from scrapy.crawler import Crawler
from sqlmodel import Session, select

from open_ire.items import AuthorItem
from open_ire.models import Author, AuthorIdentifier
from open_ire.pipelines.base_sql_model_pipeline import BaseSQLModelPipeline

logger: logging.Logger = logging.getLogger(__name__)


class AuthorIdentifierPipeline(BaseSQLModelPipeline):
    """Store author identifiers discovered during crawling.

    When a spider successfully disambiguates an author it yields an AuthorItem
    containing the author's canonical identifiers. This pipeline stores those
    identifiers, using them for deterministic author matching.

    """

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> "AuthorIdentifierPipeline":
        pipeline = cls(crawler.settings.get("OPEN_IRE_DATABASE_FILE"))
        pipeline.crawler = crawler
        return pipeline

    def process_item(self, item: Any) -> Any:
        if not isinstance(item, AuthorItem):
            return item

        if not item.identifiers:
            logger.debug("AuthorItem for '%s' has no identifiers; skipping", item.full_name)
            return item

        with Session(self.engine) as session:
            author = self._find_author_by_identifier(session, item.identifiers)

            if author:
                self._update_author(session, author, item)
            else:
                author = self._create_author_with_identifiers(session, item)

            session.commit()

        return item

    @staticmethod
    def _find_author_by_identifier(
        session: Session, identifiers: list[dict[str, str]]
    ) -> Author | None:
        """Find an existing author by any of the provided identifiers."""
        for ident in identifiers:
            existing = session.exec(
                select(AuthorIdentifier)
                .where(AuthorIdentifier.authority == ident["authority"])
                .where(AuthorIdentifier.identifier == ident["identifier"])
            ).first()
            if existing:
                return existing.author
        return None

    def _update_author(self, session: Session, author: Author, item: AuthorItem) -> None:
        """Update an existing author with new data and add any missing identifiers."""
        logger.info("Found existing author '%s' (id=%s) by identifier", author.full_name, author.id)

        author.updated_at = datetime.now()

        # Add any identifiers that don't already exist
        self._add_missing_identifiers(session, author, item.identifiers)

    @staticmethod
    def _create_author_with_identifiers(session: Session, item: AuthorItem) -> Author:
        """Create a new author with all provided identifiers."""
        author = Author(
            canonical_name=f"{item.last_name}, {item.first_name}",
            full_name=item.full_name,
            first_name=item.first_name,
            middle_names=item.middle_names,
            last_name=item.last_name,
            explicitly_searched=True,
        )
        session.add(author)
        session.flush()  # Get the ID

        logger.info(
            "Created new author '%s' (id=%s) with %s identifier(s)",
            author.full_name,
            author.id,
            len(item.identifiers),
        )

        for ident in item.identifiers:
            author_identifier = AuthorIdentifier(
                author_id=author.id,
                authority=ident["authority"],
                identifier=ident["identifier"],
            )
            session.add(author_identifier)
            logger.debug(
                "Added identifier %s:%s for author '%s'",
                ident["authority"],
                ident["identifier"],
                author.full_name,
            )

        return author

    @staticmethod
    def _add_missing_identifiers(
        session: Session,
        author: Author,
        identifiers: list[dict[str, str]],
    ) -> None:
        """Add identifiers that don't already exist for this author."""
        existing_identifiers = {(ai.authority, ai.identifier) for ai in author.identifiers}

        for ident in identifiers:
            key = (ident["authority"], ident["identifier"])
            if key not in existing_identifiers:
                author_identifier = AuthorIdentifier(
                    author_id=author.id,
                    authority=ident["authority"],
                    identifier=ident["identifier"],
                )
                session.add(author_identifier)
                logger.info(
                    "Added new identifier %s:%s for existing author '%s'",
                    ident["authority"],
                    ident["identifier"],
                    author.full_name,
                )
