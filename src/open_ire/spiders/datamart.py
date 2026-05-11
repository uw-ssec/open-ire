"""Spider to collect UW faculty author records from the Datamart.

This spider queries the UW Datamart PostgreSQL database for current faculty
records and yields one :class:`~open_ire.items.AuthorItem` per faculty row.
The :class:`~open_ire.pipelines.AuthorIdentifierPipeline` then handles
persisting each author to the local database — creating new records,
updating existing ones, and retroactively linking authors to articles
already collected by other spiders.

This spider does not make any HTTP requests. It connects directly to the
Datamart via a PostgreSQL engine and iterates over the result set locally.

Environment variables required in ``.env``::

    DB_USERNAME=<datamart_username>
    DB_PASSWORD=<datamart_password>
    DB_SERVER=<datamart_host>
    DB_PORT=<datamart_port>
    DB_DATABASE=<datamart_database>
"""

import logging
import os
from collections.abc import AsyncIterator
from typing import Any, Self

from scrapy import Spider
from sqlalchemy import create_engine as sa_create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

from open_ire.author import ParsedAuthor
from open_ire.items import AuthorItem

logger: logging.Logger = logging.getLogger(__name__)


class DatamartSpider(Spider):
    """Collect current UW faculty from the Datamart ``uw_employees`` table.

    For each faculty row where ``current_faculty_ind = 'Y'``, the spider
    yields an :class:`AuthorItem` with a ``uw_netid`` identifier.  The
    standard :class:`AuthorIdentifierPipeline` handles deduplication,
    creation, and retroactive article linking.

    Usage::

        pixi run scrapy crawl datamart
    """

    name = "datamart"

    # This spider makes no HTTP requests — disable download-related features.
    custom_settings = {  # noqa: RUF012
        "AUTOTHROTTLE_ENABLED": False,
        "DOWNLOAD_DELAY": 0,
        "ROBOTSTXT_OBEY": False,
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.datamart_engine: Engine | None = None

    # === LIFECYCLE ===

    @classmethod
    def from_crawler(cls, crawler: Any, *args: Any, **kwargs: Any) -> Self:
        """Create the spider and attach a Datamart PostgreSQL engine."""
        spider = super().from_crawler(crawler, *args, **kwargs)
        spider.datamart_engine = cls._create_datamart_engine()
        return spider

    def closed(self, reason: str) -> None:  # noqa: ARG002
        """Dispose of the Datamart engine when the spider shuts down."""
        if self.datamart_engine:
            self.datamart_engine.dispose()

    # === DATAMART CONNECTION ===

    @staticmethod
    def _create_datamart_engine() -> Engine | None:
        """Build a SQLAlchemy engine for the UW Datamart PostgreSQL database.

        Reads connection parameters from environment variables. Returns
        ``None`` if required credentials are missing.
        """
        db_user = os.getenv("DB_USERNAME")
        db_password = os.getenv("DB_PASSWORD")
        db_host = os.getenv("DB_SERVER")
        db_port = os.getenv("DB_PORT", "5432")
        db_name = os.getenv("DB_DATABASE")

        if not all([db_user, db_password, db_host, db_name]):
            logger.warning(
                "Missing Datamart credentials. Set DB_USERNAME, DB_PASSWORD, "
                "DB_SERVER, DB_PORT, and DB_DATABASE in .env"
            )
            return None

        logger.info("Connecting to Datamart at %s:%s/%s", db_host, db_port, db_name)

        db_url = f"postgresql+psycopg2://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
        return sa_create_engine(
            db_url,
            connect_args={"sslmode": "require"},
            pool_pre_ping=True,
        )

    # === FACULTY DATA LOADING ===

    def _load_faculty(self) -> list[dict[str, str]]:
        """Query current faculty from the Datamart ``uw_employees`` table.

        Returns a list of dicts with keys ``first_name``, ``last_name``,
        and ``uw_netid`` for employees where ``current_faculty_ind = 'Y'``.
        """
        if not self.datamart_engine:
            return []

        query = text("""
            SELECT e.display_first_name, e.display_last_name, e.uw_netid,
                   o.orcid_id
            FROM uw_employees e
            LEFT OUTER JOIN oris_orcids o
              ON e.uw_netid = o.uwnetid
            WHERE e.current_faculty_ind = 'Y'
        """)

        records: list[dict[str, str]] = []
        with self.datamart_engine.connect() as conn:
            for row in conn.execute(query):
                first = (row.display_first_name or "").strip()
                last = (row.display_last_name or "").strip()
                netid = (row.uw_netid or "").strip()
                orcid_id = (row.orcid_id or "").strip()
                if not first or not last or not netid:
                    continue
                records.append(
                    {
                        "first_name": first,
                        "last_name": last,
                        "uw_netid": netid,
                        "orcid_id": orcid_id,
                    }
                )

        self.logger.info("Loaded %d current faculty records from Datamart", len(records))
        return records

    # === MAIN WORKFLOW ===

    async def start(self) -> AsyncIterator[AuthorItem]:
        """Yield one :class:`AuthorItem` per current UW faculty member.

        This method does not yield any Scrapy :class:`Request` objects.
        All data comes directly from the Datamart database query.
        """
        faculty_rows = self._load_faculty()
        if not faculty_rows:
            self.logger.warning("No faculty records loaded — nothing to yield")
            return

        for row in faculty_rows:
            name = f"{row['first_name']} {row['last_name']}"
            identifiers = [
                {"authority": "uw_netid", "identifier": row["uw_netid"]},
            ]
            if row["orcid_id"]:
                identifiers.append({"authority": "orcid", "identifier": row["orcid_id"]})
            yield AuthorItem(
                author=ParsedAuthor(name),
                identifiers=identifiers,
            )

        self.logger.info("Yielded %d AuthorItem(s)", len(faculty_rows))
