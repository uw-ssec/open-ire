from collections.abc import AsyncIterator, Generator
from datetime import date
from typing import Any
from urllib.parse import urlencode, urlparse

from scrapy import Spider
from scrapy.http import Request, Response

from open_ire.author import ParsedAuthor
from open_ire.items import ArticleItem
from open_ire.settings import OPEN_IRE_DEFAULT_TERMS
from open_ire.utils import parse_date


class GSearchSpider(Spider):
    name = "gsearch"
    page_size = 100
    gsearch_url = ""
    view_detail_path = ""

    # Setting USER_AGENT=None is necessary for Scrapy to use the user agent
    # provided by Chromium when executing requests via Playwright.
    custom_settings = {"DOWNLOAD_DELAY": 10, "USER_AGENT": None}  # noqa: RUF012

    def __init__(
        self,
        terms: str = OPEN_IRE_DEFAULT_TERMS,
        page: str | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)

        if self.name == "gsearch":
            msg = "GSearchSpider should not be used directly. Please use a subclass instead."
            raise ValueError(msg)

        search_params = {"maxResults": str(self.page_size)}
        self.target_page = int(page) if page else None
        if self.target_page:
            search_params["start"] = str(self.page_size * (self.target_page - 1))

        self.start_urls = [
            f"{self.gsearch_url}?{urlencode({'terms': term.strip(), **search_params})}"
            for term in terms.split(",")
        ]

    @staticmethod
    def _extract_from_meta(response: Response, name: str) -> str | None:
        return response.xpath(f"//meta[@name='{name}']/@content").get()

    @staticmethod
    def _normalize_description_label(label: str) -> str:
        normalized_label = " ".join(label.split())
        return normalized_label.rstrip(":").strip().casefold()

    @staticmethod
    def _normalize_extracted_value(value: str) -> str | None:
        normalized_value = " ".join(value.split()).strip()
        return normalized_value or None

    @staticmethod
    def _extract_from_details_list(response: Response, label: str) -> str | None:
        expected_label = GSearchSpider._normalize_description_label(label)

        for row in response.xpath("//li[contains(@class, 'bookDetails-row')]"):
            label_text = " ".join(
                row.xpath(".//div[contains(@class, 'bookDetailsLabel')]//text()").getall()
            )
            normalized_label_text = GSearchSpider._normalize_description_label(label_text)
            if normalized_label_text != expected_label:
                continue

            value_text = " ".join(
                row.xpath(".//div[contains(@class, 'bookDetailsData')]//text()").getall()
            )
            if normalized_value := GSearchSpider._normalize_extracted_value(value_text):
                return normalized_value

        return None

    @staticmethod
    def _extract_file_urls(response: Response) -> list[str]:
        file_hrefs = response.xpath('//meta[@name="citation_pdf_url"]/@content').getall()

        return [response.urljoin(href) for href in file_hrefs if href and href.strip()]

    @staticmethod
    def _extract_authors(response: Response) -> str | None:
        authors_list = response.xpath('//meta[@name="citation_author"]/@content').getall()
        authors_list = [a.strip() for a in authors_list if a.strip()]
        if not authors_list:
            return None

        parsed_authors = [ParsedAuthor(a) for a in authors_list]
        return ParsedAuthor.encode_author_string(parsed_authors)

    def _extract_reference(self, response: Response) -> str:
        url = urlparse(response.url)

        return (
            url.path.rstrip("/").split("/")[-1]
            if url.path and url.path.startswith(self.view_detail_path)
            else response.url
        )

    def _extract_publication_date(self, response: Response) -> date | None:
        date_text = self._extract_from_meta(response, "citation_publication_date") or ""
        return parse_date(date_text)

    def _extract_extra_details(self, response: Response) -> dict[str, Any]:
        extra: dict[str, Any] = {}

        if volume := self._extract_from_meta(response, "citation_volume"):
            extra["volume"] = volume

        if publisher := self._extract_from_meta(response, "citation_publisher"):
            extra["publisher"] = publisher

        journal_title = self._extract_from_meta(response, "citation_journal_title")
        if not journal_title:
            for label in ("Journal Title", "Journal Article", "Source"):
                journal_title = self._extract_from_details_list(response, label)
                if journal_title:
                    break

        if journal_title:
            extra["journal_title"] = journal_title

        if conference := self._extract_from_meta(response, "citation_conference"):
            extra["conference"] = conference

        if language := self._extract_from_meta(response, "citation_language"):
            extra["language"] = language

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

        item = ArticleItem(
            abstract=self._extract_from_meta(response, "citation_abstract"),
            authors=self._extract_authors(response),
            doi=self._extract_from_meta(response, "citation_doi"),
            extra=self._extract_extra_details(response),
            file_urls=self._extract_file_urls(response),
            issn=self._extract_from_meta(response, "citation_issn"),
            publication_date=self._extract_publication_date(response),
            reference=self._extract_reference(response),
            repository=self.name,
            title=title,
            url=response.url,
        )

        yield item
