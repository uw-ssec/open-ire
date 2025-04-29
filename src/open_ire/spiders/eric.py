from urllib.parse import urlencode

import scrapy
from scrapy.http import Response, Request

from open_ire.items import OpenIreItem
from open_ire.settings import OPEN_IRE_DEFAULT_TERM


class EricSpider(scrapy.Spider):
    name = "eric"
    allowed_domains = ["eric.ed.gov"]

    def __init__(self, terms=OPEN_IRE_DEFAULT_TERM, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.start_urls = [
            f"https://eric.ed.gov/?{urlencode({'q': term.strip(), 'ft': 'on', 'pg': '1'})}"
            for term in terms.split(",")
        ]

    @staticmethod
    def extract_article_attribute(label: str, response: Response) -> str | None:
        value = response.xpath(f'//div[strong[contains(text(),"{label}")]]/text()').get()

        if isinstance(value, str):
            return value.strip()

        return None

    def parse(self, response: Response, **kwargs: dict):
        articles_hrefs = response.css(".r_t a::attr(href)").getall()
        for href in articles_hrefs:
            yield Request(response.urljoin(href), callback=self.parse_detail)

        next_href = response.xpath("//div/a[text()='Next Page »']/@href")
        if next_href is not None:
            yield Request(response.urljoin(next_href.get()))

    def parse_detail(self, response: Response, **kwargs: dict):
        file_href = response.urljoin(
            response.css(".r_f a[href$='.pdf']::attr(href)").get()
        )
        eric_number = self.extract_article_attribute("ERIC Number", response)
        publication_date = self.extract_article_attribute("Publication Date", response)
        eissn = self.extract_article_attribute("EISSN", response)

        item = OpenIreItem(
            abstract=response.css(".abstract::text").get(),
            authors=response.css(".r_a>div>div::text").get(),
            eissn=eissn,
            file_urls=[file_href],
            publication_date=publication_date,
            reference=eric_number,
            repository=self.name,
            title=response.css(".title::text").get(),
            url=response.url,
        )

        yield item
