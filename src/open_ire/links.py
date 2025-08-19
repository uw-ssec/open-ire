from urllib.parse import urlparse

from scrapy.http import TextResponse
from scrapy.link import Link
from scrapy.linkextractors import LinkExtractor


class ValidLinkExtractor(LinkExtractor):
    @staticmethod
    def _is_valid_url(url: str) -> bool:
        try:
            parsed = urlparse(url)
            return (
                bool(parsed.scheme)
                and bool(parsed.netloc)
                and "." in parsed.netloc
                and parsed.netloc not in ["localhost"]
                and not parsed.netloc.isdigit()
            )
        except (ValueError, TypeError):
            pass

        return False

    def extract_links(self, response: TextResponse) -> list[Link]:
        links = super().extract_links(response)

        return [link for link in links if self._is_valid_url(link.url)]
