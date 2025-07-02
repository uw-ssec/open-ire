from collections.abc import AsyncGenerator, Generator
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from dateutil.parser import parse
from scrapy import Spider
from scrapy.http import JsonRequest, JsonResponse

from open_ire.items import ArticleItem
from open_ire.settings import OPEN_IRE_DEFAULT_TERM


class OSTISpider(Spider):  # type: ignore[misc]
    name = "osti"

    def __init__(
        self,
        terms: str = OPEN_IRE_DEFAULT_TERM,
        page: str | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.start_page = page
        search_params = {
            "has_fulltext": "true",
            "page": "1" if self.start_page is None else self.start_page,
        }
        self.start_urls = [
            f"https://www.osti.gov/api/v1/records?{urlencode({'q': term.strip(), **search_params})}"
            for term in terms.split(",")
        ]

    async def start(self) -> AsyncGenerator[JsonRequest]:
        for url in self.start_urls:
            yield JsonRequest(url=url, callback=self.parse)

    def process_record(self, record: dict[str, Any]) -> ArticleItem:
        citation_url = None
        fulltext_url = None
        for link in record.get("links", []):
            if link.get("rel") == "citation":
                citation_url = link.get("href")
            elif link.get("rel") == "fulltext":
                fulltext_url = link.get("href")

        publication_date_str = record.get("publication_date")
        publication_date = parse(publication_date_str).date() if publication_date_str else None

        authors = record.get("authors", [])
        authors_str = ", ".join(authors)

        return ArticleItem(
            abstract=record.get("description"),
            authors=authors_str,
            doi=record.get("doi"),
            file_urls=[fulltext_url] if fulltext_url else [],
            publication_date=publication_date,
            reference=record.get("osti_id"),
            repository=self.name,
            title=record.get("title"),
            url=citation_url,
        )

    def parse(self, response: JsonResponse, **kwargs: Any) -> Generator[ArticleItem | JsonRequest]:  # noqa: ARG002
        records = response.json()

        for record in records:
            item = self.process_record(record)
            yield item

        if self.start_page is None and records:
            parsed_url = urlparse(response.url)
            query_params = parse_qs(parsed_url.query)
            current_page = int(query_params.get("page", ["1"])[0])

            query_params["page"] = [str(current_page + 1)]
            next_page_url = urlunparse(
                (
                    parsed_url.scheme,
                    parsed_url.netloc,
                    parsed_url.path,
                    parsed_url.params,
                    urlencode(query_params, doseq=True),
                    parsed_url.fragment,
                )
            )
            yield JsonRequest(url=next_page_url, callback=self.parse)
