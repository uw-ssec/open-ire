"""Spider to determine faculty authorship from the UW Datamart.

This spider queries the UW Datamart PostgreSQL database for current faculty
records, then matches them against article authors in the local Open IRE
database. When a match is found, it saves ``FACULTY_AUTHOR`` evidence that
supports Open Access deposit eligibility under the UW Faculty Open Access
Policy.

Unlike other OA evidence spiders (``oa_license``, ``oa_doaj``) which query
external HTTP APIs, this spider connects directly to a PostgreSQL database
and therefore does not yield Scrapy ``Request`` objects.

Environment variables required in ``.env``::

    DB_USERNAME=<datamart_username>
    DB_PASSWORD=<datamart_password>
    DB_SERVER=<datamart_host>
    DB_PORT=<datamart_port>
    DB_DATABASE=<datamart_database>
"""

import logging
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Self

from scrapy.http import Request
from sqlalchemy import create_engine as sa_create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from open_ire.author import ParsedAuthor
from open_ire.enums import DepositTransitionReason, OAEvidenceKind
from open_ire.models import Article, Author, Authorship
from open_ire.spiders.oa_evidence import BaseOAEvidenceSpider

logger: logging.Logger = logging.getLogger(__name__)


# === LOG MESSAGES ===


class LogMessages:
    """Centralized log message templates for the OA faculty spider."""

    ARTICLES_FOUND = "Found %d candidate articles for faculty author lookup"
    CONNECTING_DATAMART = "Connecting to Datamart at %s:%s/%s"
    DATAMART_CONNECTED = "Loaded %d current faculty records from Datamart"
    DATAMART_MISSING_CREDS = (
        "Missing Datamart credentials. Set DB_USERNAME, DB_PASSWORD, "
        "DB_SERVER, DB_PORT, and DB_DATABASE in .env"
    )
    EVIDENCE_SAVED = "Saved faculty_author evidence for article %s (supports_oa=%s)"
    FACULTY_MATCH = (
        "Faculty match for article %s: "
        "author '%s' matched faculty '%s %s' (employee_id=%s)"
    )
    NO_FACULTY_MATCH = "No faculty match found for article %s ('%s')"
    SKIPPING_ARTICLE = "Skipping article %s — already has faculty_author evidence"


# === DATA CLASSES ===


@dataclass(frozen=True, slots=True)
class FacultyRecord:
    """A current UW faculty member from the Datamart ``uw_employees`` table.

    Attributes
    ----------
    employee_id : str
        Unique employee identifier.
    first_name : str
        Employee's display first name.
    last_name : str
        Employee's display last name.
    uw_netid : str
        UW NETID.
    parsed : ParsedAuthor
        Pre-parsed author representation for name matching.
    """

    employee_id: str
    first_name: str
    last_name: str
    uw_netid: str
    parsed: ParsedAuthor = field(compare=False, repr=False)


@dataclass(frozen=True, slots=True)
class _ArticleCandidate:
    """An article to check for faculty authorship.

    Attributes
    ----------
    article_id : uuid.UUID
        Primary key of the article in the local database.
    title : str
        Article title (used in log messages).
    author_names : list[ParsedAuthor]
        Parsed author names associated with the article.
    """

    article_id: uuid.UUID
    title: str
    author_names: list[ParsedAuthor] = field(default_factory=list)


# === SPIDER ===


class OAFacultySpider(BaseOAEvidenceSpider):
    """Check article authors against UW Datamart faculty records.

    This spider queries the ``uw_employees`` table in the UW Datamart for
    records where ``current_faculty_ind = 'Y'``, then matches those names
    against article authors stored in the local Open IRE database.

    For each article with at least one matching faculty author, it saves
    ``FACULTY_AUTHOR`` OA evidence and (if applicable) transitions the
    article's deposit status to ``READY``.

    Usage::

        pixi run resume oa_faculty
    """

    name = "oa_faculty"

    # Override base settings: this spider makes no HTTP requests, so we disable
    # robots.txt checking, download delays, and throttling.
    custom_settings = {  # noqa: RUF012
        "AUTOTHROTTLE_ENABLED": False,
        "DOWNLOAD_DELAY": 0,
        "ITEM_PIPELINES": {},
        "ROBOTSTXT_OBEY": False,
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.datamart_engine: Engine | None = None
        self._faculty: list[FacultyRecord] = []

    # === LIFECYCLE ===

    @classmethod
    def from_crawler(cls, crawler: Any, *args: Any, **kwargs: Any) -> Self:
        """Initialize the spider with both the local DB and Datamart engines."""
        spider = super().from_crawler(crawler, *args, **kwargs)
        spider.datamart_engine = cls._create_datamart_engine()
        return spider

    def closed(self, reason: str) -> None:  # noqa: ARG002
        """Dispose of both database engines on spider close."""
        if self.engine:
            self.engine.dispose()
        if self.datamart_engine:
            self.datamart_engine.dispose()

    # === DATAMART CONNECTION ===

    @staticmethod
    def _create_datamart_engine() -> Engine | None:
        """Create a SQLAlchemy engine for the UW Datamart PostgreSQL database.

        Reads connection parameters from environment variables. Returns
        ``None`` if required credentials are missing.
        """
        db_user = os.getenv("DB_USERNAME")
        db_password = os.getenv("DB_PASSWORD")
        db_host = os.getenv("DB_SERVER")
        db_port = os.getenv("DB_PORT", "5432")
        db_name = os.getenv("DB_DATABASE")

        if not all([db_user, db_password, db_host, db_name]):
            logger.warning(LogMessages.DATAMART_MISSING_CREDS)
            return None

        logger.info(LogMessages.CONNECTING_DATAMART, db_host, db_port, db_name)

        db_url = (
            f"postgresql+psycopg2://{db_user}:{db_password}"
            f"@{db_host}:{db_port}/{db_name}"
        )
        return sa_create_engine(
            db_url,
            connect_args={"sslmode": "require"},
            pool_pre_ping=True,
        )

    # === FACULTY DATA LOADING ===

    def _load_faculty(self) -> list[FacultyRecord]:
        """Load current faculty records from the Datamart ``uw_employees`` table.

        Returns a list of ``FacultyRecord`` objects for employees where
        ``current_faculty_ind = 'Y'``.
        """
        if not self.datamart_engine:
            return []

        query = text("""
            SELECT employee_id, display_first_name, display_last_name, uw_netid
            FROM uw_employees
            WHERE current_faculty_ind = 'Y'
        """)

        records: list[FacultyRecord] = []
        with self.datamart_engine.connect() as conn:
            result = conn.execute(query)
            for row in result:
                first = (row.display_first_name or "").strip()
                last = (row.display_last_name or "").strip()
                if not first or not last:
                    continue

                records.append(
                    FacultyRecord(
                        employee_id=(row.employee_id or "").strip(),
                        first_name=first,
                        last_name=last,
                        uw_netid=(row.uw_netid or "").strip(),
                        parsed=ParsedAuthor(f"{first} {last}"),
                    )
                )

        self.logger.info(LogMessages.DATAMART_CONNECTED, len(records))
        return records

    # === ARTICLE CANDIDATE LOADING ===

    def _query_article_candidates(self) -> list[_ArticleCandidate]:
        """Query articles that do not yet have ``FACULTY_AUTHOR`` evidence.

        For each candidate article, this method collects author names from
        two sources:

        1. The ``authorship`` join table (structured author records).
        2. The ``authors`` text field on the article (semicolon-separated
           string, used as a fallback for articles without structured
           authorship records).
        """
        if not self.engine:
            return []

        candidates: list[_ArticleCandidate] = []

        with Session(self.engine) as session:
            articles = session.exec(select(Article)).all()

            for article in articles:
                # Skip articles that already have faculty_author evidence
                if self.has_oa_evidence(
                    article.id,
                    kind=OAEvidenceKind.FACULTY_AUTHOR,
                    sources=["uw_datamart"],
                ):
                    self.logger.debug(LogMessages.SKIPPING_ARTICLE, article.id)
                    continue

                # Collect author names from the authorship join table
                author_names: list[ParsedAuthor] = []

                authorships = session.exec(
                    select(Authorship).where(Authorship.article_id == article.id)
                ).all()

                for authorship in authorships:
                    author_record = session.get(Author, authorship.author_id)
                    if author_record and author_record.canonical_name:
                        author_names.append(ParsedAuthor(author_record.canonical_name))

                # Fallback: parse the semicolon-separated authors string
                if not author_names and article.authors:
                    author_names = ParsedAuthor.parse_author_string(article.authors)

                if author_names:
                    candidates.append(
                        _ArticleCandidate(
                            article_id=article.id,
                            title=article.title,
                            author_names=author_names,
                        )
                    )

        self.logger.info(LogMessages.ARTICLES_FOUND, len(candidates))
        return candidates

    # === MATCHING ===

    def _find_faculty_match(
        self,
        author_names: list[ParsedAuthor],
    ) -> tuple[ParsedAuthor, FacultyRecord] | None:
        """Find the first faculty match among the article's authors.

        Compares each article author against all loaded faculty records using
        ``ParsedAuthor.likely_same()``, which handles initials, prefix
        matching, and diacritic normalization.

        Returns a tuple of ``(article_author, matched_faculty)`` or ``None``
        if no match is found.
        """
        for author in author_names:
            for faculty in self._faculty:
                if author.likely_same(faculty.parsed):
                    return (author, faculty)
        return None

    # === EVIDENCE SAVING ===

    def _save_faculty_evidence(
        self,
        article_id: uuid.UUID,
        article_author: ParsedAuthor,
        faculty: FacultyRecord,
    ) -> None:
        """Save ``FACULTY_AUTHOR`` OA evidence for an article.

        Parameters
        ----------
        article_id
            The article to save evidence for.
        article_author
            The article author that matched the faculty record.
        faculty
            The matched Datamart faculty record.
        """
        self.save_oa_evidence(
            article_id,
            kind=OAEvidenceKind.FACULTY_AUTHOR,
            source="uw_datamart",
            supports_oa=True,
            data={
                "matched_author": article_author.canonical_name,
                "faculty_employee_id": faculty.employee_id,
                "faculty_first_name": faculty.first_name,
                "faculty_last_name": faculty.last_name,
                "faculty_uw_netid": faculty.uw_netid,
            },
            transition_reason=DepositTransitionReason.FACULTY_AUTHOR,
        )
        self.logger.info(
            LogMessages.FACULTY_MATCH,
            article_id,
            article_author.canonical_name,
            faculty.first_name,
            faculty.last_name,
            faculty.employee_id,
        )

    # === MAIN WORKFLOW ===

    async def start(self) -> AsyncIterator[Request]:
        """Main entry point for the spider.

        This spider does not make any HTTP requests. Instead it:

        1. Loads current faculty records from the Datamart.
        2. Queries the local database for candidate articles.
        3. Matches article authors against faculty records.
        4. Saves ``FACULTY_AUTHOR`` evidence for matched articles.

        Yields nothing (no Scrapy Requests are generated).
        """
        # Load faculty records from the Datamart
        self._faculty = self._load_faculty()
        if not self._faculty:
            self.logger.warning("No faculty records loaded — aborting")
            return

        # Get candidate articles from the local database
        candidates = self._query_article_candidates()
        if not candidates:
            self.logger.info("No candidate articles found — nothing to do")
            return

        # Match each article's authors against faculty records
        matched_count = 0
        for candidate in candidates:
            match = self._find_faculty_match(candidate.author_names)
            if match:
                article_author, faculty = match
                self._save_faculty_evidence(
                    candidate.article_id, article_author, faculty
                )
                matched_count += 1
            else:
                self.logger.debug(
                    LogMessages.NO_FACULTY_MATCH,
                    candidate.article_id,
                    candidate.title[:60],
                )

        self.logger.info(
            "Faculty matching complete: %d of %d articles matched",
            matched_count,
            len(candidates),
        )

        # AsyncIterator requires yielding; we have nothing to yield since
        # all work is done via direct DB operations.
        return  # noqa: RET504
        yield  # type: ignore[misc]  # Make this a valid AsyncIterator
