import json
from collections.abc import Generator
from datetime import date
from typing import Any
from urllib.parse import urlencode

from dateutil.parser import parse
from scrapy.http import Request, Response

from open_ire.items import ArticleItem
from open_ire.settings import OPENALEX_CONTACT_EMAIL, OPENALEX_INSTITUTION_ID
from open_ire.spiders.search import AuthorSearchSpider


class OpenAlexSpider(AuthorSearchSpider):
    """
    OpenAlex API spider for collecting academic publications by author.
    """

    name = "openalex"
    base_url = "https://api.openalex.org"
    page_size = 25

    def __init__(
        self,
        start_date: str = "2018-01-01",
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)

        self.start_date = start_date
        self.institution_id = OPENALEX_INSTITUTION_ID
        self.request_headers: dict[str, str] = {"User-Agent": f"mailto:{OPENALEX_CONTACT_EMAIL}"}

    # === HIGH-LEVEL WORKFLOW METHODS ===
    # These methods define the main crawling workflow

    def build_search_request(self, term: str) -> Request:
        """Build the initial search request for a given author name."""
        params = {
            "filter": f"display_name.search:{term},last_known_institutions.id:{self.institution_id}",
            "per_page": str(self.page_size),
        }
        url = f"{self.base_url}/authors?{urlencode(params)}"

        return Request(
            url,
            headers=self.request_headers,
            callback=self.author_publication_requests,
        )

    def author_publication_requests(self, response: Response) -> Generator[Request, None, None]:
        """Parse author search results and generate publication requests."""
        data = json.loads(response.text or "{}")

        for author in data.get("results", []):
            author_id = author.get("id")
            if not author_id:
                continue

            # TODO: OpenAlex returns a relevance score; we could use it for early filtering.

            yield from self._request_publications(author_id)

    # === SUPPORTING WORKFLOW METHODS ===
    # These methods support the main workflow

    def _request_publications(
        self, author_id: str, cursor: str = "*"
    ) -> Generator[Request, None, None]:
        """Generate a request for an author's publications with pagination support."""
        params = {
            "filter": f"author.id:{author_id},from_publication_date:{self.start_date}",
            "per_page": str(self.page_size),
            "cursor": cursor,
            "sort": "publication_date:desc",
        }
        url = f"{self.base_url}/works?{urlencode(params)}"

        yield Request(
            url,
            headers=self.request_headers,
            callback=self.parse_publications,
            cb_kwargs={"author_id": author_id},
        )

    def parse_publications(
        self, response: Response, author_id: str
    ) -> Generator[Request | ArticleItem, None, None]:
        """Parse publication results and yield ArticleItems, handling pagination."""
        data = json.loads(response.text or "{}")
        results = data.get("results", [])

        for publication in results:
            if not isinstance(publication, dict):
                continue

            if item := self._build_item(publication):
                yield item

        meta = data.get("meta", {})
        if next_cursor := meta.get("next_cursor"):
            yield from self._request_publications(author_id, cursor=next_cursor)

    def _build_item(self, publication: dict[str, Any]) -> ArticleItem | None:
        """Build an ArticleItem from OpenAlex publication data."""
        external_id = publication.get("id")
        if not external_id:
            return None

        author_names = self._extract_authors(publication)
        oa_status = publication.get("open_access", {}).get("oa_status")
        is_oa = publication.get("open_access", {}).get("is_oa")

        return ArticleItem(
            authors=self._join_authors(author_names),
            doi=publication.get("doi"),
            extra={
                "is_open_access": is_oa,
                "journal_name": self._extract_journal_name(publication),
                "oa_status": oa_status,
                "publication_type": publication.get("type"),
                "publication_year": self._parse_year(publication.get("publication_year")),
            },
            publication_date=self._parse_date(publication.get("publication_date")),
            reference=str(external_id),
            repository=self.name,
            title=publication.get("title"),
            url=publication.get("doi"),
        )

    # === DATA EXTRACTION UTILITIES ===
    # These methods extract specific data from OpenAlex API responses

    @staticmethod
    def _extract_authors(publication: dict[str, Any]) -> list[str]:
        """Extract author names from publication authorship data."""
        author_names: list[str] = []
        authorships = publication.get("authorships", [])
        for authorship in authorships:
            if not isinstance(authorship, dict):
                continue

            display_name = authorship.get("author", {}).get("display_name")
            if display_name and isinstance(display_name, str):
                author_names.append(display_name)

        return author_names

    @staticmethod
    def _extract_journal_name(publication: dict[str, Any]) -> str | None:
        """Extract journal name from publication location data."""
        primary_location = publication.get("primary_location") or {}
        if isinstance(primary_location, dict):
            source = primary_location.get("source") or {}
            if isinstance(source, dict) and source.get("display_name"):
                return str(source["display_name"])

        for location in publication.get("locations", []) or []:
            if not isinstance(location, dict):
                continue

            source = location.get("source") or {}
            display_name = source.get("display_name")
            if display_name and isinstance(display_name, str):
                return str(display_name)

        return None

    # === LOW-LEVEL UTILITY FUNCTIONS ===
    # These are generic utility functions for data processing

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
        """Parse a year value into an integer, returning None on failure."""
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
        return None
