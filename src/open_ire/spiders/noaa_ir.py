from collections.abc import Generator
from typing import Any
from urllib.parse import urlencode

from dateutil.parser import parse
from scrapy import Spider
from scrapy.http import Request, Response

from open_ire.items import ArticleItem
from open_ire.settings import OPEN_IRE_DEFAULT_TERMS


class NOAASpider(Spider):
    name = "noaa_ir"
    page_count = 100

    def __init__(
        self,
        terms: str = OPEN_IRE_DEFAULT_TERMS,
        page: str | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        search_params = {"maxResults": str(self.page_count)}

        self.target_page = int(page) if page else None
        if self.target_page:
            search_params["start"] = str(self.page_count * (self.target_page - 1))

        self.start_urls = [
            (
                "https://repository.library.noaa.gov/gsearch?"
                f"{urlencode({'terms': term.strip(), **search_params})}"
            )
            for term in terms.split(",")
        ]

    @staticmethod
    def extract_file_urls(response: Response) -> list[str]:
        file_hrefs = response.xpath('//meta[@name="citation_pdf_url"]/@content').getall()

        urls = []
        for href in file_hrefs:
            urls.append(response.urljoin(href))

        return urls

    @staticmethod
    def extract_extra_details(response: Response) -> dict[str, Any]:
        extra: dict[str, Any] = {}

        if volume := response.xpath('//meta[@name="citation_volume"]/@content').get():
            extra["volume"] = volume
        if publisher := response.xpath('//meta[@name="citation_publisher"]/@content').get():
            extra["publisher"] = publisher
        if journal_title := response.xpath('//meta[@name="citation_journal_title"]/@content').get():
            extra["journal_title"] = journal_title
        if conference := response.xpath('//meta[@name="citation_conference"]/@content').get():
            extra["conference"] = conference
        if language := response.xpath('//meta[@name="citation_language"]/@content').get():
            extra["language"] = language
        if citation_text := response.xpath('//textarea[@id="Genericpreview"]/text()').get():
            extra["citation_text"] = citation_text.strip()
        if keywords := response.xpath('//meta[@name="citation_keywords"]/@content').getall():
            extra["keywords"] = list({k.strip() for k in keywords if k and k.strip()})

        return extra

    def parse(self, response: Response, **kwargs: Any) -> Generator[Request]:  # noqa: ARG002
        articles_hrefs = response.xpath(
            '//div[contains(@class, "search-result-row")]//div[contains(@class, "object-title")]/a/@href'
        ).getall()
        for href in articles_hrefs:
            yield Request(response.urljoin(href), callback=self.parse_detail)

        if self.target_page is None:
            next_href = response.xpath('//a[contains(@class, "arrow-cont")]/@href').get()
            if next_href is not None:
                yield Request(response.urljoin(next_href))

    def parse_detail(self, response: Response) -> Generator[ArticleItem]:
        reference = response.url.strip("/").split("/")[-1]
        title = response.xpath('//meta[@name="citation_title"]/@content').get()
        abstract = response.xpath('//meta[@name="citation_abstract"]/@content').get()
        doi = response.xpath('//meta[@name="citation_doi"]/@content').get()
        authors = response.xpath('//meta[@name="citation_author"]/@content').getall()
        publication_date_text = (
            response.xpath('//meta[@name="citation_publication_date"]/@content').get() or ""
        )
        issn = response.xpath('//meta[@name="citation_issn"]/@content').get()

        try:
            publication_date = parse(publication_date_text).date()
        except (ValueError, TypeError):
            publication_date = None

        item = ArticleItem(
            abstract=abstract,
            authors=", ".join(authors),
            doi=doi,
            extra=self.extract_extra_details(response),
            file_urls=self.extract_file_urls(response),
            issn=issn,
            publication_date=publication_date,
            reference=reference,
            repository=self.name,
            title=title,
            url=response.url,
        )

        yield item
