import json
import pytest
from pathlib import Path
from scrapy.http import HtmlResponse, Request
from typing import Any
from urllib.parse import urlparse, parse_qs

from open_ire.author import AuthorRecord
from open_ire.enums import ArticleType
from open_ire.items import ArticleItem
from open_ire.spiders.openalex import OpenAlexSpider


@pytest.fixture
def spider(tmp_path: Path, five_authors: list[AuthorRecord]) -> OpenAlexSpider:
    csv_content = f"""Full Name,FirstName,LastName,Email
{five_authors[4].full_name},{five_authors[4].first_name},{five_authors[4].last_name},{five_authors[4].email}
"""
    csv_path = tmp_path / "adeyemi.csv"
    csv_path.write_text(csv_content)

    return OpenAlexSpider(author_csv=str(csv_path))


@pytest.fixture
def five_authors() -> list[AuthorRecord]:
    return [
        AuthorRecord("Luis Manuel Garcia-Mispireta"),
        AuthorRecord("E.V.S.S.K. Babu"),
        AuthorRecord("Ramón H. Rivera-Servera"),
        AuthorRecord("M. Elena Alvarez-Alvarez"),
        AuthorRecord("Kemi Adeyemi", email="kadeyemi@uw.edu"),
    ]


@pytest.fixture
def dummy_publication(five_authors) -> dict[str, Any]:
    return {
        "id": "W123",
        "title": "A Study on Testing",
        "publication_date": "2022-05-15",
        "primary_location": {"source": {"display_name": "Journal of Testing"}},
        "authorships": [
            {"author": {"display_name": five_authors[0].full_name}},
            {"author": {"display_name": five_authors[1].full_name}},
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

    def test_extract_authors(self, five_authors, dummy_publication) -> None:
        authors = OpenAlexSpider._extract_authors(dummy_publication)
        assert authors == [five_authors[0], five_authors[1]]

        authors = OpenAlexSpider._extract_authors({})
        assert authors == []

    @pytest.mark.parametrize(
        "author_id,cursor",
        [
            ("A1", "*"),
            ("A2", "cursor123"),
        ],
    )
    def test_request_publications(self, spider, five_authors, author_id, cursor) -> None:
        requests = list(
            spider._request_publications(author_id, five_authors[0].normalized_name, cursor)
        )
        assert len(requests) == 1
        assert requests[0].url.startswith(spider.base_url + "/works")
        assert requests[0].cb_kwargs["author_id"] == author_id
        assert requests[0].meta["matched_author"] == five_authors[0].normalized_name

        parsed_url = urlparse(requests[0].url)
        query_params = parse_qs(parsed_url.query)
        assert query_params["cursor"] == [cursor]

    def test_author_publication_requests(self, spider, five_authors) -> None:
        response_data = {"results": [{"id": "A1"}, {"id": "A2"}]}
        request = Request(
            url="http://dummy.url", meta={"matched_author": five_authors[0].normalized_name}
        )
        response = HtmlResponse(
            url="http://dummy.url",
            body=json.dumps(response_data),
            encoding="utf-8",
            request=request,
        )

        requests = list(spider.author_publication_requests(response))
        assert len(requests) == 2
        assert all(isinstance(req, Request) for req in requests)
        assert requests[0].url.startswith(spider.base_url + "/works")
        assert requests[1].url.startswith(spider.base_url + "/works")

    def test_parse_publications(self, spider, five_authors, dummy_publication) -> None:
        publication_data = {
            "results": [
                {
                    **dummy_publication,
                },
                "invalid publication",
            ],
            "meta": {"next_cursor": "cursor123"},
        }
        request = Request(
            url="http://dummy.url", meta={"matched_author": five_authors[0].normalized_name}
        )
        response = HtmlResponse(
            url="http://dummy.url",
            body=json.dumps(publication_data),
            encoding="utf-8",
            request=request,
        )

        emitted = list(spider.parse_publications(response, author_id="A1"))
        assert isinstance(emitted[0], ArticleItem)
        assert isinstance(emitted[1], Request)

    def test_build_item(self, spider, five_authors, dummy_publication) -> None:
        item = spider._build_item(dummy_publication, five_authors[0].normalized_name)
        assert isinstance(item, ArticleItem)
        assert (
            item.doi == "https://doi.org/10.1234/test.doi"
        )  # Spider returns original DOI, pipeline normalizes it
        assert item.title == "A Study on Testing"
        assert item.extra["journal_name"] == "Journal of Testing"
        assert item.extra["matched_author"] == five_authors[0].normalized_name
        assert item.authors == AuthorRecord.encode_author_string([five_authors[0], five_authors[1]])

    def test_build_search_request(self, spider, five_authors) -> None:
        request = spider.build_search_request(five_authors[0].normalized_name)
        assert isinstance(request, Request)
        assert request.url.startswith(spider.base_url + "/authors")
        parsed_url = urlparse(request.url)
        query_params = parse_qs(parsed_url.query)
        assert query_params["search"] == [five_authors[0].normalized_name]
        assert "affiliations.institution.id:" in query_params["filter"][0]

    @pytest.mark.asyncio
    async def test_start(self, spider) -> None:
        requests = []
        async for req in spider.start():
            requests.append(req)
        assert len(requests) == 1
        assert isinstance(requests[0], Request)
        assert "Kemi+Adeyemi" in requests[0].url

    @pytest.mark.parametrize(
        "raw_type,expected",
        [
            ("article", ArticleType.SCHOLARLY_ARTICLE),
            ("preprint", ArticleType.SCHOLARLY_ARTICLE),
            ("proceedings-article", ArticleType.SCHOLARLY_ARTICLE),
            ("posted-content", ArticleType.SCHOLARLY_ARTICLE),
            ("review", ArticleType.SCHOLARLY_ARTICLE),
            ("book", ArticleType.OTHER),
            ("book-chapter", ArticleType.OTHER),
            ("editorial", ArticleType.OTHER),
            ("erratum", ArticleType.OTHER),
            ("letter", ArticleType.OTHER),
            ("libguides", ArticleType.OTHER),
            ("paratext", ArticleType.OTHER),
            ("supplementary-materials", ArticleType.OTHER),
        ],
    )
    def test_normalize_type_known_types(self, raw_type: str, expected: ArticleType) -> None:
        assert OpenAlexSpider._normalize_type(raw_type) == expected

    @pytest.mark.parametrize("raw_type", ["Article", "ARTICLE", "PrePrint", "BOOK"])
    def test_normalize_type_case_insensitive(self, raw_type: str) -> None:
        result = OpenAlexSpider._normalize_type(raw_type)
        assert result is not None

    def test_normalize_type_none(self) -> None:
        assert OpenAlexSpider._normalize_type(None) is None

    def test_normalize_type_unknown(self) -> None:
        assert OpenAlexSpider._normalize_type("unknown-type") is None
