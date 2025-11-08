import json
import pytest
from pathlib import Path
from scrapy.http import HtmlResponse, Request
from urllib.parse import urlparse, parse_qs

from open_ire.items import OAPPublicationItem
from open_ire.spiders.oap_openalex import OAPOpenAlexSpider

@pytest.fixture
def dummy_csv(tmp_path: Path) -> Path:
    csv_content = """Full Name,FirstName,LastName,Email
Amina ElSayed,Amina,ElSayed,amina.elsayed@example.edu
"""
    csv_path = tmp_path / "dummy.csv"
    csv_path.write_text(csv_content)
    return csv_path

class TestOAPOpenAlexSpider:
    def test_extract_journal_name(self) -> None:
        primary_location = {"source": {"display_name": "Journal of Testing"}}
        locations = [ {"source": {"display_name": "Alternate Journal Name"}} ]
        publication_with_primary = {
            "primary_location": primary_location,
            "locations": locations
        }
        publication_without_primary = {
            "primary_location": None,
            "locations": locations
        }

        journal_name = OAPOpenAlexSpider._extract_journal_name(publication_with_primary)
        assert journal_name == "Journal of Testing"

        journal_name = OAPOpenAlexSpider._extract_journal_name(publication_without_primary)
        assert journal_name == "Alternate Journal Name"

        journal_name = OAPOpenAlexSpider._extract_journal_name({})
        assert journal_name == None

        journal_name = OAPOpenAlexSpider._extract_journal_name({
            "primary_location": "invalid data",
            "locations": [ "invalid location" ]
        })
        assert journal_name == None

    def test_extract_authors(self) -> None:
        publication = {
            "authorships": [
                {"author": {"display_name": "Alice Smith"}},
                {"author": {"display_name": "Bob Jones"}},
                {"author": {}},
                "invalid authorship"
            ]
        }

        authors = OAPOpenAlexSpider._extract_authors(publication)
        assert authors == ["Alice Smith", "Bob Jones"]

        authors = OAPOpenAlexSpider._extract_authors({})
        assert authors == []

    @pytest.mark.parametrize(
        "author_id,cursor",
        [
            ("A1", "*"),
            ("A2", "cursor123"),
        ]
    )
    def test_request_publications(self, dummy_csv, author_id, cursor) -> None:
        spider = OAPOpenAlexSpider(faculty_csv=str(dummy_csv))
        requests = list(spider._request_publications(author_id, cursor))
        assert len(requests) == 1
        assert requests[0].url.startswith(spider.base_url + "/works")
        assert requests[0].cb_kwargs["author_id"] == author_id

        parsed_url = urlparse(requests[0].url)
        query_params = parse_qs(parsed_url.query)
        assert query_params["cursor"] == [cursor]

    def test_author_publication_requests(self, dummy_csv) -> None:
        spider = OAPOpenAlexSpider(faculty_csv=str(dummy_csv))
        response_data = {
            "results": [
                {"id": "A1"},
                {"id": "A2"}
            ]
        }
        response = HtmlResponse(
            url="http://dummy.url",
            body=json.dumps(response_data),
            encoding="utf-8"
        )

        requests = list(spider.author_publication_requests(response))
        assert len(requests) == 2
        assert all(isinstance(req, Request) for req in requests)
        assert requests[0].url.startswith(spider.base_url + "/works")
        assert requests[1].url.startswith(spider.base_url + "/works")

    def test_parse_publications(self, dummy_csv) -> None:
        spider = OAPOpenAlexSpider(faculty_csv=str(dummy_csv))
        publication_data = {
            "results": [
                {
                    "id": "W123",
                    "title": "A Study on Testing",
                    "primary_location": {
                        "source": {"display_name": "Journal of Testing"}
                    },
                    "authorships": [
                        {"author": {"display_name": "Alice Smith"}},
                        {"author": {"display_name": "Bob Jones"}}
                    ],
                    "doi": "10.1234/test.doi"
                },
                "invalid publication"
            ],
            "meta": {
                "next_cursor": "cursor123"
            }
        }
        response = HtmlResponse(
            url="http://dummy.url",
            body=json.dumps(publication_data),
            encoding="utf-8"
        )

        emitted = list(spider.parse_publications(response, author_id="A1"))
        assert isinstance(emitted[0], OAPPublicationItem)
        assert isinstance(emitted[1], Request)

    def test_build_item(self, dummy_csv) -> None:
        spider = OAPOpenAlexSpider(faculty_csv=str(dummy_csv))
        publication = {
            "id": "W123",
            "title": "A Study on Testing",
            "primary_location": {
                "source": {"display_name": "Journal of Testing"}
            },
            "authorships": [
                {"author": {"display_name": "Alice Smith"}},
                {"author": {"display_name": "Bob Jones"}}
            ],
            "doi": "10.1234/test.doi"
        }

        item = spider._build_item(publication)
        assert isinstance(item, OAPPublicationItem)
        assert item.doi == "10.1234/test.doi"
        assert item.title == "A Study on Testing"
        assert item.journal_name == "Journal of Testing"
        assert item.authors == "Alice Smith, Bob Jones"

    @pytest.mark.asyncio
    async def test_start(self, dummy_csv) -> None:
        spider = OAPOpenAlexSpider(faculty_csv=str(dummy_csv))
        requests = []
        async for req in spider.start():
            requests.append(req)

        assert len(requests) == 1
        assert requests[0].url.startswith(spider.base_url + "/authors")
