from typing import Any

from scrapy.http import Response

from open_ire.spiders.gsearch import GSearchSpider


class NOAASpider(GSearchSpider):
    name = "noaa"
    gsearch_url = "https://repository.library.noaa.gov/gsearch"
    view_detail_path = "/view/noaa/"

    def _extract_extra_details(self, response: Response) -> dict[str, Any]:
        extra: dict[str, Any] = super()._extract_extra_details(response)

        if citation_text := response.xpath('//textarea[@id="Genericpreview"]/text()').get():
            extra["citation_text"] = citation_text.strip()

        return extra
