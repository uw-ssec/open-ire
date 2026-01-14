import json
import pytest
from pathlib import Path
from scrapy.http import HtmlResponse, Request
from typing import Any
from urllib.parse import urlparse, parse_qs

from open_ire.items import ArticleItem
from open_ire.spiders.openalex import OpenAlexSpider


@pytest.fixture
def spider(tmp_path: Path) -> OpenAlexSpider:
    csv_content = """Full Name,FirstName,LastName,Email
Kemi Adeyemi,Kemi,Adeyemi,kadeyemi@uw.edu
"""
    csv_path = tmp_path / "adeyemi.csv"
    csv_path.write_text(csv_content)

    return OpenAlexSpider(author_csv=str(csv_path))


@pytest.fixture
def dummy_publication() -> dict[str, Any]:
    return {
        "id": "W123",
        "title": "A Study on Testing",
        "publication_date": "2022-05-15",
        "primary_location": {"source": {"display_name": "Journal of Testing"}},
        "authorships": [
            {"author": {"display_name": "Alice Smith"}},
            {"author": {"display_name": "Bob Jones"}},
        ],
        "doi": "https://doi.org/10.1234/test.doi",
    }


class TestOpenAlexSpider:
    def test_extract_journal_name(self) -> None:
        primary_location = {"source": {"display_name": "Journal of Testing"}}
        locations = [{"source": {"display_name": "Alternate Journal Name"}}]
        publication_with_primary = {"primary_location": primary_location, "locations": locations}
        publication_without_primary = {"primary_location": None, "locations": locations}

        journal_name = OpenAlexSpider._extract_journal_name(publication_with_primary)
        assert journal_name == "Journal of Testing"

        journal_name = OpenAlexSpider._extract_journal_name(publication_without_primary)
        assert journal_name == "Alternate Journal Name"

        journal_name = OpenAlexSpider._extract_journal_name({})
        assert journal_name == None

        journal_name = OpenAlexSpider._extract_journal_name(
            {"primary_location": "invalid data", "locations": ["invalid location"]}
        )
        assert journal_name == None

    def test_extract_authors(self) -> None:
        publication = {
            "authorships": [
                {"author": {"display_name": "Alice Smith"}},
                {"author": {"display_name": "Bob Jones"}},
                {"author": {}},
                "invalid authorship",
            ]
        }

        authors = OpenAlexSpider._extract_authors(publication)
        assert authors == ["Alice Smith", "Bob Jones"]

        authors = OpenAlexSpider._extract_authors({})
        assert authors == []

    @pytest.mark.parametrize(
        "author_id,cursor",
        [
            ("A1", "*"),
            ("A2", "cursor123"),
        ],
    )
    def test_request_publications(self, spider, author_id, cursor) -> None:
        requests = list(spider._request_publications(author_id, cursor))
        assert len(requests) == 1
        assert requests[0].url.startswith(spider.base_url + "/works")
        assert requests[0].cb_kwargs["author_id"] == author_id

        parsed_url = urlparse(requests[0].url)
        query_params = parse_qs(parsed_url.query)
        assert query_params["cursor"] == [cursor]

    def test_author_publication_requests(self, spider) -> None:
        response_data = {"results": [{"id": "A1"}, {"id": "A2"}]}
        response = HtmlResponse(
            url="http://dummy.url", body=json.dumps(response_data), encoding="utf-8"
        )

        requests = list(spider.author_publication_requests(response))
        assert len(requests) == 2
        assert all(isinstance(req, Request) for req in requests)
        assert requests[0].url.startswith(spider.base_url + "/works")
        assert requests[1].url.startswith(spider.base_url + "/works")

    def test_parse_publications(self, spider, dummy_publication) -> None:
        publication_data = {
            "results": [
                {
                    **dummy_publication,
                },
                "invalid publication",
            ],
            "meta": {"next_cursor": "cursor123"},
        }
        response = HtmlResponse(
            url="http://dummy.url", body=json.dumps(publication_data), encoding="utf-8"
        )

        emitted = list(spider.parse_publications(response, author_id="A1"))
        assert isinstance(emitted[0], ArticleItem)
        assert isinstance(emitted[1], Request)

    def test_build_item(self, spider, dummy_publication) -> None:
        item = spider._build_item(dummy_publication)
        assert isinstance(item, ArticleItem)
        assert item.doi == "https://doi.org/10.1234/test.doi"  # Spider returns original DOI, pipeline normalizes it
        assert item.title == "A Study on Testing"
        assert item.extra["journal_name"] == "Journal of Testing"
        assert item.authors == "Alice Smith; Bob Jones"

    def test_build_search_request(self, spider) -> None:
        request = spider.build_search_request("Kemi Adeyemi")
        assert isinstance(request, Request)
        assert request.url.startswith(spider.base_url + "/authors")
        parsed_url = urlparse(request.url)
        query_params = parse_qs(parsed_url.query)
        assert "display_name.search:Kemi Adeyemi" in query_params["filter"][0]

    @pytest.mark.asyncio
    async def test_start(self, spider) -> None:
        requests = []
        async for req in spider.start():
            requests.append(req)
        assert len(requests) == 1
        assert isinstance(requests[0], Request)
        assert "Kemi+Adeyemi" in requests[0].url
