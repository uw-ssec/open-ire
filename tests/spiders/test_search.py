from urllib.parse import parse_qs, urlencode, urlparse

import pytest
from scrapy.http import Request

from open_ire.settings import OPEN_IRE_DEFAULT_TERMS
from open_ire.spiders.search import TermSearchSpider


class DummyTermSearchSpider(TermSearchSpider):
    name = "dummy-term-search"

    def build_search_request(self, term: str) -> Request:
        return Request(f"https://example.test/search?{urlencode({'term': term})}")


async def _collect_requests(spider: DummyTermSearchSpider) -> list[Request]:
    outputs: list[Request] = []
    async for output in spider.start():
        assert isinstance(output, Request)
        outputs.append(output)
    return outputs


class TestTermSearchSpider:
    @pytest.mark.asyncio
    async def test_start_skips_empty_terms(self) -> None:
        spider = DummyTermSearchSpider(terms="alpha, ,beta,,")
        requests = await _collect_requests(spider)

        assert len(requests) == 2
        assert [parse_qs(urlparse(req.url).query)["term"][0] for req in requests] == [
            "alpha",
            "beta",
        ]

    def test_uses_default_terms_when_no_input(self) -> None:
        spider = DummyTermSearchSpider()
        assert spider.search_phrases == [term.strip() for term in OPEN_IRE_DEFAULT_TERMS.split(",")]
