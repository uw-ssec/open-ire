from collections.abc import Generator
from typing import Any, cast
from urllib.parse import urlencode, urlparse

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
        self.dataset_link_extractor = ValidLinkExtractor(
            allow=r"^http",
            deny_domains=["epa.gov"],
            restrict_xpaths="//div[contains(@class, 'node-page')]",
        )

    def extract_file_urls(self, response: TextResponse) -> list[str]:
        links = self.file_link_extractor.extract_links(response)
        return [link.url for link in links]

    def extract_dataset_urls(self, response: TextResponse) -> list[str]:
        article_page_text = response.xpath("//div[contains(@class, 'node-page')]//text()").getall()
        full_text = " ".join(article_page_text).upper()

        if "DATA/SOFTWARE" in full_text:
            links = self.dataset_link_extractor.extract_links(response)
            return [link.url for link in links]

        return []

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

    def parse_detail(self, response: Response, **kwargs: Any) -> Generator[Request | ArticleItem]:  # noqa: ARG002
        text_response = cast(TextResponse, response)
        title = text_response.css('meta[name="DC.title"]::attr(content)').get()
        abstract = text_response.css('meta[name="DC.description"]::attr(content)').get()
        reference = text_response.xpath('//span[@id="recordID"]/text()').get()
        publication_date_text = (
            text_response.xpath(
                "//b[contains(text(), 'Product Published Date:')]/following-sibling::text()"
            ).get()
            or text_response.css('meta[name="DC.date.created"]::attr(content)').get()
        ) or ""

        publication_date = parse_date(publication_date_text)

        item = ArticleItem(
            abstract=abstract,
            authors=self.extract_authors(text_response, title or ""),
            file_urls=self.extract_file_urls(text_response),
            publication_date=publication_date,
            reference=reference,
            repository=self.name,
            title=title,
            url=text_response.url,
        )

        dataset_urls = self.extract_dataset_urls(text_response)
        if dataset_urls:
            url = dataset_urls.pop()
            yield Request(
                url,
                callback=self.parse_datagov_detail,
                meta={"item": item, "dataset_urls": dataset_urls},
            )
        else:
            yield item

    def parse_datagov_detail(
        self,
        response: Response,
        **kwargs: Any,  # noqa: ARG002
    ) -> Generator[Request | ArticleItem]:
        text_response = cast(TextResponse, response)
        file_reference_urls = text_response.meta.get("file_reference_urls", [])
        if "data.gov" in urlparse(text_response.url.lower()).netloc:
            hrefs = text_response.xpath(
                "//ul[@class='resource-list']//a[@class='btn btn-primary'][contains(., 'Download')]/@href"
            ).getall()
            hrefs = [text_response.urljoin(href) for href in hrefs]
            file_reference_urls = file_reference_urls + [
                (text_response.url, href) for href in hrefs
            ]

        next_url = None
        if dataset_urls := text_response.meta.get("dataset_urls", []):
            next_url = dataset_urls.pop()

        item = text_response.meta["item"]
        item.file_reference_urls = file_reference_urls

        if next_url:
            yield Request(
                next_url,
                callback=self.parse_datagov_detail,
                meta={
                    "item": item,
                    "dataset_urls": dataset_urls,
                    "file_reference_urls": file_reference_urls,
                },
            )
        else:
            yield item
