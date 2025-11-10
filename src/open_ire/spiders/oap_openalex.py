import json
from collections.abc import AsyncIterator, Generator
from datetime import date
from typing import Any
from urllib.parse import urlencode

from dateutil.parser import parse
from scrapy import Spider
from scrapy.http import Request, Response

from open_ire.faculty import AuthorMatcher
from open_ire.items import ArticleItem
from open_ire.settings import OAP_OPENALEX_CONTACT_EMAIL, OAP_OPENALEX_INSTITUTION_ID


class OAPOpenAlexSpider(Spider):
    name = "oap_openalex"
    base_url = "https://api.openalex.org"
    page_size = 25

    def __init__(
        self,
        faculty_csv: str,
        start_date: str = "2018-01-01",
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)

        if not faculty_csv:
            msg = "The 'faculty_csv' argument is required."
            raise ValueError(msg)

        self.start_date = start_date
        self.institution_id = OAP_OPENALEX_INSTITUTION_ID
        self.request_headers: dict[str, str] = {
            "User-Agent": f"mailto:{OAP_OPENALEX_CONTACT_EMAIL}"
        }
        self.author_matcher = AuthorMatcher(faculty_csv, "openalex")
        self.faculty_names = list(self.author_matcher.faculty_lookup["raw"].keys())

    @staticmethod
    def _join_or_none(values: list[str]) -> str | None:
        return ", ".join(values) if values else None

    @staticmethod
    def _parse_date(value: Any) -> date | None:
        if not value:
            return None
        try:
            return parse(str(value)).date()
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _parse_year(value: Any) -> int | None:
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
        return None

    @staticmethod
    def _extract_journal_name(publication: dict[str, Any]) -> str | None:
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

    @staticmethod
    def _extract_authors(publication: dict[str, Any]) -> list[str]:
        author_names: list[str] = []
        authorships = publication.get("authorships", [])
        for authorship in authorships:
            if not isinstance(authorship, dict):
                continue

            display_name = authorship.get("author", {}).get("display_name")
            if display_name and isinstance(display_name, str):
                author_names.append(display_name)

        return author_names

    def _request_publications(
        self, author_id: str, cursor: str = "*"
    ) -> Generator[Request, None, None]:
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

    async def start(self) -> AsyncIterator[Request]:
        """Generate initial requests to search for authors by name within the institution."""
        for name in self.faculty_names:
            params = {
                "filter": f"display_name.search:{name},last_known_institutions.id:{self.institution_id}",
                "per_page": str(self.page_size),
            }
            url = f"{self.base_url}/authors?{urlencode(params)}"

            yield Request(
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

    def parse_publications(
        self, response: Response, author_id: str
    ) -> Generator[Request | ArticleItem, None, None]:
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
        external_id = publication.get("id")
        if not external_id:
            return None

        author_names = self._extract_authors(publication)

        matched_names, matched_emails = self.author_matcher.collect_matches(author_names)

        oa_status = publication.get("open_access", {}).get("oa_status")
        is_oa = publication.get("open_access", {}).get("is_oa")

        return ArticleItem(
            authors=self._join_or_none(author_names),
            doi=publication.get("doi"),
            extra={
                "is_open_access": is_oa,
                "journal_name": self._extract_journal_name(publication),
                "matched_author": self._join_or_none(matched_names),
                "matched_email": self._join_or_none(matched_emails),
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
