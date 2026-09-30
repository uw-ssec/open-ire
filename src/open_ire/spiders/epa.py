from collections.abc import Generator
from typing import Any, cast
from urllib.parse import urlencode

from scrapy import Spider
from scrapy.http import Request, Response, TextResponse

from open_ire.items import ArticleItem
from open_ire.links import ValidLinkExtractor
from open_ire.settings import OPEN_IRE_DEFAULT_TERMS
from open_ire.utils import parse_date


class EPASpider(Spider):
    name = "epa"
    page_size = 25
    custom_settings = {"ROBOTSTXT_OBEY": False}  # noqa: RUF012

    def __init__(
        self,
        terms: str = OPEN_IRE_DEFAULT_TERMS,
        page: str | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        search_params = {"count": str(self.page_size)}

        self.target_page = int(page) if page else None
        if self.target_page:
            search_params["startIndex"] = str(self.page_size * (self.target_page - 1) + 1)

        self.start_urls = [
            (
                "https://cfpub.epa.gov/si/si_public_search_results.cfm?"
                f"{urlencode({'searchall': term.strip(), **search_params})}"
            )
            for term in terms.split(",")
        ]

        self.file_link_extractor = ValidLinkExtractor(
            allow=r"si_public_file_download\.cfm",
        )

    def extract_file_urls(self, response: TextResponse) -> list[str]:
        links = self.file_link_extractor.extract_links(response)
        return [link.url for link in links]

    @staticmethod
    def extract_authors(response: TextResponse, title: str) -> str | None:
        citation = response.xpath("//h2[text()='Citation:']/following-sibling::p/text()").get()

        if citation and len(citation):
            authors = citation.split(title)[0].strip()
            return authors if len(authors) > 1 else None

        return None

    def parse(self, response: Response, **kwargs: Any) -> Generator[Request]:  # noqa: ARG002
        text_response = cast(TextResponse, response)
        articles_hrefs = text_response.xpath(
            "//a[starts-with(@href, 'si_public_record_report.cfm')]/@href"
        ).getall()
        for href in articles_hrefs:
            yield Request(text_response.urljoin(href), callback=self.parse_detail)

        if self.target_page is None:
            next_href = text_response.xpath('//a[contains(text(), "Next")]/@href').get()
            if next_href is not None:
                yield Request(text_response.urljoin(next_href))

    def parse_detail(self, response: Response, **kwargs: Any) -> Generator[ArticleItem]:  # noqa: ARG002
        text_response = cast(TextResponse, response)
        title = text_response.css('meta[name="DC.title"]::attr(content)').get()
        abstract = text_response.css('meta[name="DC.description"]::attr(content)').get()
        reference = text_response.xpath('//span[@id="recordID"]/text()').get()
        if not reference or not title:
            self.logger.warning(
                "Skipping article with missing title or record ID: %s", response.url
            )
            return

        publication_date_text = (
            text_response.xpath(
                "//b[contains(text(), 'Product Published Date:')]/following-sibling::text()"
            ).get()
            or text_response.css('meta[name="DC.date.created"]::attr(content)').get()
        ) or ""

        publication_date = parse_date(publication_date_text)

        item = ArticleItem(
            abstract=abstract,
            authors=self.extract_authors(text_response, title),
            file_urls=self.extract_file_urls(text_response),
            publication_date=publication_date,
            reference=reference,
            repository=self.name,
            title=title,
            url=text_response.url,
        )

        yield item
