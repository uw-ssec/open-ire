from collections.abc import Generator
from typing import Any
from urllib.parse import urlencode

from dateutil.parser import parse
from scrapy import Spider
from scrapy.http import Request, Response

from open_ire.items import ArticleItem
from open_ire.settings import OPEN_IRE_DEFAULT_TERM


class EPASpider(Spider):
    name = "epa"
    page_count = 25
    custom_settings = {"ROBOTSTXT_OBEY": False}  # noqa: RUF012

    def __init__(
        self,
        terms: str = OPEN_IRE_DEFAULT_TERM,
        page: str | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        search_params = {"count": str(self.page_count)}

        self.target_page = int(page) if page else None
        if self.target_page:
            search_params["startIndex"] = str(self.page_count * (self.target_page - 1) + 1)

        self.start_urls = [
            (
                "https://cfpub.epa.gov/si/si_public_search_results.cfm?"
                f"{urlencode({'keyword': term.strip(), **search_params})}"
            )
            for term in terms.split(",")
        ]

    @staticmethod
    def extract_file_urls(response: Response) -> list[str]:
        file_hrefs = response.xpath(
            "//a[starts-with(@href, 'si_public_file_download.cfm')]/@href"
        ).getall()
        gov_hrefs = response.xpath(
            "//div[contains(@class, 'node-page')]//a[starts-with(@href, 'http') and contains(@href, '.gov') and not(contains(@href, 'epa.gov'))]/@href"
        ).getall()

        urls = [response.urljoin(href) for href in file_hrefs]

        return urls + gov_hrefs

    @staticmethod
    def extract_authors(response: Response, title: str) -> str | None:
        citation = response.xpath("//h2[text()='Citation:']/following-sibling::p/text()").get()

        if citation and len(citation):
            authors = citation.split(title)[0].strip()
            return authors if len(authors) > 1 else None

        return None

    def parse(self, response: Response, **kwargs: Any) -> Generator[Request]:  # noqa: ARG002
        articles_hrefs = response.xpath(
            "//a[starts-with(@href, 'si_public_record_report.cfm')]/@href"
        ).getall()
        for href in articles_hrefs:
            yield Request(response.urljoin(href), callback=self.parse_detail)

        if self.target_page is None:
            next_href = response.xpath('//a[contains(text(), "Next")]/@href').get()
            if next_href is not None:
                yield Request(response.urljoin(next_href))

    def parse_detail(self, response: Response) -> Generator[ArticleItem]:
        title = response.css('meta[name="DC.title"]::attr(content)').get()
        abstract = response.css('meta[name="DC.description"]::attr(content)').get()
        reference = response.xpath('//span[@id="recordID"]/text()').get()
        publication_date_text = (
            response.xpath(
                "//b[contains(text(), 'Product Published Date:')]/following-sibling::text()"
            ).get()
            or response.css('meta[name="DC.date.created"]::attr(content)').get()
        ) or ""

        try:
            publication_date = parse(publication_date_text).date()
        except ValueError:
            publication_date = None

        item = ArticleItem(
            abstract=abstract,
            authors=self.extract_authors(response, title or ""),
            file_urls=self.extract_file_urls(response),
            publication_date=publication_date,
            reference=reference,
            repository=self.name,
            title=title,
            url=response.url,
        )

        yield item
