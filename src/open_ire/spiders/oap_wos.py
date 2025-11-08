import datetime
import json
import os
from collections.abc import AsyncIterator, Generator
from typing import Any
from urllib.parse import urlencode

from scrapy.http import Request, Response

from open_ire.items import OAPPublicationItem
from open_ire.settings import OAP_WOS_ORGANIZATION
from open_ire.spiders.oap_base import OAPBaseSpider


class OAPWoSSpider(OAPBaseSpider):
    name = "oap_wos"
    base_url = "https://api.clarivate.com/api/wos/"

    def __init__(
        self,
        faculty_csv: str,
        start_year: str = "2018",
        end_year: str | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(faculty_csv, *args, **kwargs)

        current_year = datetime.date.today().year
        self.organization = OAP_WOS_ORGANIZATION
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
        self.query = self._build_query()

    @staticmethod
    def _validate_year(raw_year: str, field_name: str) -> int:
        try:
            value = int(raw_year)
        except (TypeError, ValueError) as exc:
            msg = f"Invalid value for '{field_name}': {raw_year!r}"
            raise ValueError(msg) from exc

        return value

    @staticmethod
    def _extract_authors(names: list[Any]) -> list[str]:
        authors: list[str] = []
        for author in names:
            if not isinstance(author, dict):
                continue

            full_name = author.get("wos_standard") or author.get("display_name")
            if full_name:
                authors.append(str(full_name))

        return authors

    @staticmethod
    def _extract_journal_name(titles: list[Any]) -> str | None:
        for title in titles:
            if not isinstance(title, dict):
                continue
            if title.get("type") == "source" and title.get("content"):
                return str(title["content"])

        return None

    @staticmethod
    def _as_list(value: Any) -> list[Any]:
        if isinstance(value, list):
            return value

        if value is None:
            return []

        return [value]

    def _build_query(self) -> str:
        authors = list(self.faculty_lookup["raw"].keys())
        author_clause = " OR ".join(f'"{name.title()}"' for name in authors)

        return (
            f'AU=({author_clause}) AND OG=("{self.organization}") '
            f"AND PY=({self.start_year}-{self.end_year})"
        )

    def _build_params(self, page: int) -> dict[str, Any]:
        return {
            "count": self.page_size,
            "databaseId": "WOS",
            "page": page,
            "sortField": "PY+D",
            "usrQuery": self.query,
        }

    async def start(self) -> AsyncIterator[Request]:
        params = self._build_params(page=1)
        url = f"{self.base_url}?{urlencode(params)}"

        yield Request(
            url,
            headers=self.headers,
            callback=self.parse_publications,
            cb_kwargs={"page": 1},
        )

    def parse_publications(
        self, response: Response, page: int
    ) -> Generator[Request | OAPPublicationItem]:
        data = json.loads(response.text or "{}")
        records = self._as_list(
            data.get("Data", {}).get("Records", {}).get("records", {}).get("REC")
        )
        total = data.get("metadata", {}).get("total", 0)

        emitted = 0
        for record in records:
            if item := self._build_item(record):
                emitted += 1
                yield item

        if (page - 1) * self.page_size + emitted < total:
            next_page = page + 1
            params = self._build_params(page=next_page)
            next_url = f"{self.base_url}?{urlencode(params)}"

            yield Request(
                next_url,
                headers=self.headers,
                callback=self.parse_publications,
                cb_kwargs={"page": next_page},
            )

    def _build_item(self, publication: Any) -> OAPPublicationItem | None:
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
        matched_names, matched_emails = self._collect_matches(authors)

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

        return OAPPublicationItem(
            authors=self._join_or_none(authors),
            doi=doi,
            external_id=str(external_id),
            journal_name=self._extract_journal_name(titles),
            matched_author=self._join_or_none(matched_names),
            matched_email=self._join_or_none(matched_emails),
            publication_date=self._parse_date(
                pub_info.get("coverdate") or pub_info.get("sortdate")
            ),
            publication_type=summary.get("doctypes", {}).get("doctype"),
            publication_year=self._parse_year(pub_info.get("pubyear") or pub_info.get("coverdate")),
            repository=self.repository_name,
            title=title,
        )
