from collections.abc import AsyncIterator, Generator
from datetime import date
from typing import Any
from urllib.parse import urlencode, urlparse

from dateutil.parser import parse
from scrapy import Spider
from scrapy.http import Request, Response

from open_ire.items import ArticleItem
from open_ire.settings import OPEN_IRE_DEFAULT_TERMS


class CDCStacksSpider(Spider):
    name = "cdc_stacks"
    page_count = 20
    custom_settings = {"DOWNLOAD_DELAY": 10, "USER_AGENT": None}  # noqa: RUF012

    def __init__(
        self,
        terms: str = OPEN_IRE_DEFAULT_TERMS,
        page: str | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)

        search_params = {}
        self.target_page = int(page) if page else None
        if self.target_page:
            search_params["start"] = str(self.page_count * (self.target_page - 1) + 1)

        self.start_urls = [
            f"https://stacks.cdc.gov/gsearch?{urlencode({'terms': term.strip(), **search_params})}"
            for term in terms.split(",")
        ]

    @staticmethod
    def _extract_from_meta(response: Response, name: str) -> str | None:
        return response.xpath(f"//meta[@name='{name}']/@content").get()

    @staticmethod
    def _extract_authors(response: Response) -> str | None:
        authors_list = response.xpath('//meta[@name="citation_author"]/@content').getall()
        authors_list = [a.strip() for a in authors_list if a.strip()]
        if not authors_list:
            return None

        return ", ".join(authors_list)

    @staticmethod
    def _extract_reference(response: Response) -> str:
        url = urlparse(response.url)

        return (
            url.path.rstrip("/").split("/")[-1]
            if url.path and url.path.startswith("/view/cdc/")
            else response.url
        )

    def _extract_publication_date(self, response: Response) -> date | None:
        date_text = self._extract_from_meta(response, "citation_publication_date") or ""
        try:
            return parse(date_text).date()
        except (ValueError, TypeError):
            pass

        return None

    def _extract_extra_details(self, response: Response) -> dict[str, Any]:
        extra: dict[str, Any] = {}

        if volume := self._extract_from_meta(response, "citation_volume"):
            extra["volume"] = volume
        if publisher := self._extract_from_meta(response, "citation_publisher"):
            extra["publisher"] = publisher
        if keywords := response.xpath('//meta[@name="citation_keywords"]/@content').getall():
            extra["keywords"] = list({k.strip() for k in keywords if k and k.strip()})

        return extra

    async def start(self) -> AsyncIterator[Any]:
        for url in self.start_urls:
            yield Request(url, dont_filter=True, meta={"playwright": True})

    def parse(self, response: Response, **kwargs: Any) -> Generator[Request]:  # noqa: ARG002
        articles_hrefs = response.xpath("//div[@class='object-title']/a/@href").getall()
        for href in articles_hrefs:
            yield Request(
                response.urljoin(href),
                callback=self.parse_detail,
                meta={"playwright": True},
            )

        if self.target_page is None:
            next_href = response.xpath("//a[@id='nextPage']/@href").get()
            if next_href is not None:
                yield Request(response.urljoin(next_href), meta={"playwright": True})

    def parse_detail(self, response: Response) -> Generator[ArticleItem]:
        title = self._extract_from_meta(response, "citation_title") or ""
        pdf_url = self._extract_from_meta(response, "citation_pdf_url")
        file_urls = [response.urljoin(pdf_url)] if pdf_url else []

        item = ArticleItem(
            abstract=self._extract_from_meta(response, "citation_abstract"),
            authors=self._extract_authors(response),
            doi=self._extract_from_meta(response, "citation_doi"),
            extra=self._extract_extra_details(response),
            file_urls=file_urls,
            issn=self._extract_from_meta(response, "citation_issn"),
            publication_date=self._extract_publication_date(response),
            reference=self._extract_reference(response),
            repository=self.name,
            title=title,
            url=response.url,
        )

        yield item
