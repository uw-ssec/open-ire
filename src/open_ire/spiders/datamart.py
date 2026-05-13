"""Spider to collect UW faculty author records from the UW Libraries data mart.

This spider queries the UW Libraries data mart PostgreSQL database for current
faculty records and yields one :class:`~open_ire.items.AuthorItem` per faculty
row.

This spider does not make any HTTP requests. It connects directly to the
Datamart via a PostgreSQL engine and iterates over the result set locally.

Environment variables required in ``.env``::

    DATAMART_USER=<datamart_username>
    DATAMART_PASS=<datamart_password>
    DATAMART_HOST=<datamart_host>
    DATAMART_PORT=<datamart_port>
    DATAMART_DB=<datamart_database>
"""

import logging
import os
from collections.abc import AsyncIterator
from typing import Any

from scrapy import Spider
from sqlalchemy import create_engine as sa_create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

from open_ire.author import ParsedAuthor
from open_ire.items import AuthorItem

logger: logging.Logger = logging.getLogger(__name__)


class DatamartSpider(Spider):
    """Collect information about current UW faculty from the UW Libraries data mart."""

    name = "datamart"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.datamart_engine: Engine = self._create_datamart_engine()

    def closed(self, reason: str) -> None:  # noqa: ARG002
        """Dispose of the datamart engine when the spider shuts down."""
        self.datamart_engine.dispose()

    # === DATAMART CONNECTION ===

    @staticmethod
    def _create_datamart_engine() -> Engine:
        """Build a SQLAlchemy engine for the data mart PostgreSQL database.

        Reads connection parameters from environment variables. Raises
        RuntimeError if required credentials are missing.
        """
        dm_user = os.getenv("DATAMART_USER")
        dm_pass = os.getenv("DATAMART_PASS")
        dm_host = os.getenv("DATAMART_HOST")
        dm_port = os.getenv("DATAMART_PORT", "5432")
        dm_name = os.getenv("DATAMART_DB")

        if not all([dm_user, dm_pass, dm_host, dm_name]):
            msg = """Missing data mart credentials. Set DATAMART_USER, DATAMART_PASS,
                  DATAMART_HOST, DATAMART_PORT (if not 5432), and DATAMART_DB in .env"""
            raise RuntimeError(msg)

        logger.info("Connecting to %s:%s/%s", dm_host, dm_port, dm_name)

        db_url = f"postgresql+psycopg2://{dm_user}:{dm_pass}@{dm_host}:{dm_port}/{dm_name}"
        return sa_create_engine(
            db_url,
            connect_args={"sslmode": "require"},
            pool_pre_ping=True,
        )

    # === FACULTY DATA LOADING ===

    def _load_faculty(self) -> list[dict[str, str]]:
        """Query current faculty from the data mart.

        Returns a list of dicts with keys ``first_name``, ``last_name``,
        ``uw_netid``, and (if available) ``orcid_id``."""
        query = text("""
            SELECT e.display_first_name, e.display_last_name, e.uw_netid,
                   o."ORCID_ID" AS orcid_id
            FROM uw_employees e
            LEFT OUTER JOIN oris_orcids o
              ON e.uw_netid = o."UWNetID"
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

        self.logger.info("Loaded %d current faculty records", len(records))
        return records

    # === MAIN WORKFLOW ===

    async def start(self) -> AsyncIterator[AuthorItem]:
        """Yield one :class:`AuthorItem` per current UW faculty member.

        This method does not yield any Scrapy :class:`Request` objects.
        All data comes directly from the Datamart database query.
        """
        faculty_rows = self._load_faculty()
        if not faculty_rows:
            self.logger.warning("No faculty records loaded")
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
