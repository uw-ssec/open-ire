import logging
from datetime import datetime
from typing import Any

from sqlmodel import Session, select

from open_ire.author import ParsedAuthor
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

    def process_item(self, item: Any) -> Any:
        if not isinstance(item, AuthorItem):
            return item

        with Session(self.engine) as session:
            by_identifier = self._find_author_by_identifier(session, item.identifiers)
            by_name = self._find_author_by_name(session, item)
            candidates = [a for a in (by_identifier, by_name) if a is not None]

            if not candidates:
                self._create_author_with_identifiers(session, item)
            elif len(candidates) == 1 or (candidates[0].id == candidates[1].id):
                self._update_author(session, candidates[0], item)
            else:
                raise RuntimeError("Multiple existing authors found for " + item.full_name)

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
                logger.info(
                    "Found existing author '%s' (id=%s) by identifier",
                    existing.author.full_name,
                    existing.author.id,
                )
                return existing.author
        return None

    @staticmethod
    def _find_author_by_name(session: Session, item: AuthorItem) -> Author | None:
        """Find an existing author by compatible parsed name."""
        parsed = ParsedAuthor(item.full_name)
        last_name = (item.last_name or parsed.last_name).strip()
        if not last_name:
            return None

        candidates = session.exec(select(Author).where(Author.last_name == last_name)).all()
        target = ParsedAuthor(item.full_name)
        for candidate in candidates:
            if ParsedAuthor(candidate.canonical_name).likely_same(target):
                logger.info(
                    "Found existing author '%s' (id=%s) by name",
                    candidate.full_name,
                    candidate.id,
                )
                return candidate
        return None

    def _update_author(self, session: Session, author: Author, item: AuthorItem) -> None:
        """Update an existing author with new data and add any missing identifiers."""
        author.updated_at = datetime.now()

        # Add any identifiers that don't already exist
        self._add_missing_identifiers(session, author, item.identifiers)

    @staticmethod
    def _create_author_with_identifiers(session: Session, item: AuthorItem) -> Author:
        """Create a new author with all provided identifiers."""
        parsed = ParsedAuthor(item.full_name)
        first_name = item.first_name or parsed.first_name or None
        middle_names = item.middle_names or parsed.middle_names or None
        last_name = item.last_name or parsed.last_name or None

        canonical_name = ParsedAuthor(
            " ".join(part for part in [first_name, middle_names, last_name] if part)
        ).canonical_name

        author = Author(
            canonical_name=canonical_name,
            full_name=item.full_name,
            first_name=first_name,
            middle_names=middle_names,
            last_name=last_name,
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
