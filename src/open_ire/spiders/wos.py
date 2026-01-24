import datetime
import json
import os
import re
from collections.abc import Generator
from datetime import date
from typing import Any
from urllib.parse import urlencode

from dateutil.parser import parse
from scrapy.http import Request, Response

from open_ire.author import AuthorRecord
from open_ire.items import ArticleItem
from open_ire.settings import WOS_ORGANIZATION
from open_ire.spiders.search import AuthorSearchSpider


class WoSSpider(AuthorSearchSpider):
    """
    Web of Science API spider for collecting academic publications by author.
    """

    name = "wos"
    base_url = "https://api.clarivate.com/api/wos/"
    page_size = 25

    def __init__(
        self,
        start_year: str = "2018",
        end_year: str | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)

        current_year = datetime.date.today().year
        self.organization = WOS_ORGANIZATION
        self.start_year = self._validate_year(start_year, "start_year")

        if end_year is None:
            self.end_year = current_year
        else:
            self.end_year = self._validate_year(end_year, "end_year")

        if self.end_year < self.start_year:
            msg = "The 'end_year' must be greater than or equal to 'start_year'."
            raise ValueError(msg)

        self.api_key = os.getenv("WOS_API_KEY") or ""
        if not self.api_key:
            msg = "Missing Web of Science API key. Set the WOS_API_KEY environment variable."
            raise ValueError(msg)

        self.headers = {"X-ApiKey": self.api_key}

    def _get_author_name(self, record: AuthorRecord) -> str:
        return f"{record.last_name}, {record.first_name}"

    # === HIGH-LEVEL WORKFLOW METHODS ===
    # These methods define the main crawling workflow

    def build_search_request(self, term: str) -> Request:
        """Build a search request for a single author term."""
        query = self._build_query(term)
        params = self._build_params(query, page=1)
        url = f"{self.base_url}?{urlencode(params)}"

        return Request(
            url,
            headers=self.headers,
            callback=self.parse_publications,
            meta={"matched_author": term},
            cb_kwargs={"query": query, "page": 1},
        )

    def parse_publications(
        self, response: Response, query: str, page: int
    ) -> Generator[Request | ArticleItem, None, None]:
        """Parse WoS publication results and yield ArticleItems, handling pagination."""
        matched_author = response.meta["matched_author"]
        raw_text = response.text or ""

        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError:
            self.logger.error("WoS returned non-JSON body (first 500 chars): %r", raw_text[:500])
            return

        if not isinstance(data, dict):
            self.logger.warning(
                "Unexpected WoS payload type for query %r: %s",
                query,
                type(data).__name__,
            )
            return

        records_container = (data.get("Data") or {}).get("Records", {}).get("records")

        # WoS "no results" => records: "" (string). Sometimes errors also show up as strings.
        if isinstance(records_container, str):
            if not records_container:
                self.logger.info("No records found for query: %r", query)
            else:
                self.logger.warning(
                    "Unexpected WoS records payload (string) for query %r: %r",
                    query,
                    records_container[:200],
                )
            return

        if not isinstance(records_container, dict):
            self.logger.info(
                "WoS returned no usable records container for query %r (type=%s)",
                query,
                type(records_container).__name__,
            )
            return

        records = self._as_list(records_container.get("REC"))

        try:
            total = int((data.get("QueryResult") or {}).get("RecordsFound") or 0)
        except (TypeError, ValueError):
            total = 0

        emitted = 0
        for record in records:
            if item := self._build_item(record, matched_author):
                emitted += 1
                yield item

        if (page - 1) * self.page_size + emitted < total:
            next_page = page + 1
            params = self._build_params(query, page=next_page)
            next_url = f"{self.base_url}?{urlencode(params)}"

            yield Request(
                next_url,
                headers=self.headers,
                callback=self.parse_publications,
                meta={"matched_author": matched_author},
                cb_kwargs={"query": query, "page": next_page},
            )

    # === SUPPORTING WORKFLOW METHODS ===
    # These methods support the main workflow

    def _build_query(self, term: str) -> str:
        """Build WoS query string with author, organization, and date filters."""
        return (
            f'AU=("{term}") AND OG=("{self.organization}") '
            f"AND PY=({self.start_year}-{self.end_year})"
        )

    def _build_params(self, query: str, page: int) -> dict[str, Any]:
        """Build API request parameters for WoS search."""
        return {
            "count": self.page_size,
            "databaseId": "WOS",
            "page": page,
            "sortField": "PY+D",
            "usrQuery": query,
        }

    def _build_item(self, publication: Any, matched_author: str) -> ArticleItem | None:
        """Build an ArticleItem from WoS publication data."""
        if not isinstance(publication, dict):
            return None

        external_id = publication.get("UID")
        if not external_id:
            return None

        summary = publication.get("static_data", {}).get("summary", {})
        titles = self._as_list(summary.get("titles", {}).get("title"))
        title = next(
            (t.get("content") for t in titles if isinstance(t, dict) and t.get("type") == "item"),
            None,
        )

        names = self._as_list(summary.get("names", {}).get("name"))
        authors = self._extract_authors(names)

        pub_info = summary.get("pub_info", {})
        cluster_related = publication.get("dynamic_data", {}).get("cluster_related", {})
        identifiers = self._as_list(cluster_related.get("identifiers", {}).get("identifier"))
        doi = next(
            (
                identifier.get("value")
                for identifier in identifiers
                if identifier.get("type") == "doi"
            ),
            None,
        )

        return ArticleItem(
            authors=AuthorRecord.encode_author_string(authors),
            doi=doi,
            extra={
                "journal_name": self._extract_journal_name(titles),
                "publication_type": summary.get("doctypes", {}).get("doctype"),
                "publication_year": self._parse_year(
                    pub_info.get("pubyear") or pub_info.get("coverdate")
                ),
                "matched_author": matched_author,
            },
            publication_date=self._parse_date(
                pub_info.get("coverdate") or pub_info.get("sortdate")
            ),
            reference=str(external_id),
            repository=self.name,
            title=title,
            url=f"https://doi.org/{doi}"
            if doi
            else f"https://www.webofscience.com/wos/woscc/full-record/{external_id}",
        )

    # === DATA EXTRACTION UTILITIES ===
    # These methods extract specific data from WoS API responses

    @staticmethod
    def _extract_authors(names: list[Any]) -> list[AuthorRecord]:
        """Extract author names from WoS names data structure."""
        authors: list[AuthorRecord] = []
        for author in names:
            if not isinstance(author, dict):
                continue

            author_name = (
                author.get("full_name") or author.get("display_name") or author.get("wos_standard")
            )
            if author_name:
                authors.append(AuthorRecord(author_name))

        return authors

    @staticmethod
    def _extract_journal_name(titles: list[Any]) -> str | None:
        """Extract journal name from WoS titles data structure."""
        for title in titles:
            if not isinstance(title, dict):
                continue
            if title.get("type") == "source" and title.get("content"):
                return str(title["content"])

        return None

    # === LOW-LEVEL UTILITY FUNCTIONS ===
    # These are generic utility functions for data processing

    @staticmethod
    def _as_list(value: Any) -> list[Any]:
        """Convert a value to a list, handling WoS API's inconsistent list/single item responses."""
        if isinstance(value, list):
            return value

        if value is None:
            return []

        return [value]

    @staticmethod
    def _parse_date(value: Any) -> date | None:
        """Parse a date value into a date object, returning None on failure."""
        if not value:
            return None
        try:
            return parse(str(value)).date()
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _parse_year(value: Any) -> int | None:
        """Parse a year value into an integer, with regex fallback for complex formats."""
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            pass
        match = re.search(r"(19|20)\d{2}", str(value))
        if match:
            return int(match.group())
        return None

    @staticmethod
    def _validate_year(raw_year: str, field_name: str) -> int:
        """Validate and convert a year string to integer, raising ValueError on failure."""
        try:
            value = int(raw_year)
        except (TypeError, ValueError) as exc:
            msg = f"Invalid value for '{field_name}': {raw_year!r}"
            raise ValueError(msg) from exc

        return value
