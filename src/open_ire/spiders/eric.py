from __future__ import annotations

from collections.abc import Generator
from typing import Any
from urllib.parse import urlencode

from dateutil.parser import parse
from scrapy import Spider
from scrapy.http import Request, Response

from open_ire.items import ArticleItem
from open_ire.settings import OPEN_IRE_DEFAULT_TERM


class EricSpider(Spider):  # type: ignore[misc]
    name = "eric"

    def __init__(
        self,
        terms: str = OPEN_IRE_DEFAULT_TERM,
        page: str | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.page = page
        search_params = {"ft": "on", "pg": "1" if self.page is None else self.page}
        self.start_urls = [
            f"https://eric.ed.gov/?{urlencode({'q': term.strip(), **search_params})}"
            for term in terms.split(",")
        ]

    @staticmethod
    def extract_article_attribute(label: str, response: Response) -> str | None:
        value = response.xpath(f'//div[strong[contains(text(),"{label}")]]/text()').get()

        if isinstance(value, str):
            return value.strip()

        return None

    def parse(self, response: Response, **kwargs: Any) -> Generator[Request]:  # noqa: ARG002
        articles_hrefs = response.css(".r_t a::attr(href)").getall()
        for href in articles_hrefs:
            yield Request(response.urljoin(href), callback=self.parse_detail)

        if self.page is None:
            next_href = response.xpath("//div/a[text()='Next Page »']/@href")
            if next_href is not None:
                yield Request(response.urljoin(next_href.get()))

    def parse_detail(self, response: Response) -> Generator[ArticleItem]:
        file_href = response.urljoin(response.css(".r_f a[href$='.pdf']::attr(href)").get())
        eric_number = self.extract_article_attribute("ERIC Number", response)
        publication_date = self.extract_article_attribute("Publication Date", response) or ""
        eissn = self.extract_article_attribute("EISSN", response)

        item = ArticleItem(
            abstract=response.css(".abstract::text").get(),
            authors=response.css(".r_a>div>div::text").get(),
            eissn=eissn,
            file_urls=[file_href],
            publication_date=parse(publication_date).date(),
            reference=eric_number,
            repository=self.name,
            title=response.css(".title::text").get(),
            url=response.url,
        )

        yield item
