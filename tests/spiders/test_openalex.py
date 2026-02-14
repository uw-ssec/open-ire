import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from scrapy.http import HtmlResponse, Request

from open_ire.author import ParsedAuthor
from open_ire.enums import ArticleType
from open_ire.items import ArticleItem
from open_ire.spiders.openalex import OpenAlexSpider


@pytest.fixture
def sample_authors() -> list[ParsedAuthor]:
    return [
        ParsedAuthor("Luis Manuel Garcia-Mispireta"),
        ParsedAuthor("E.V.S.S.K. Babu"),
        ParsedAuthor("Ramón H. Rivera-Servera"),
        ParsedAuthor("M. Elena Alvarez-Alvarez"),
        ParsedAuthor("Kemi Adeyemi", email="kadeyemi@uw.edu"),
    ]


@pytest.fixture
def make_csv_for_author(tmp_path: Path) -> Callable[[ParsedAuthor], Path]:
    def _make(author: ParsedAuthor) -> Path:
        csv_path = tmp_path / f"{author.last_name}.csv"
        csv_path.write_text(
            f"Full Name,FirstName,MiddleNames,LastName,Email\n"
            f"{author.full_name},{author.first_name},{author.middle_names},{author.last_name},{author.email or ''}\n"
        )
        return csv_path

    return _make


@pytest.fixture
def make_spider_from_csv(
    make_csv_for_author: Callable[[ParsedAuthor], Path],
) -> Callable[[ParsedAuthor], OpenAlexSpider]:
    def _make(author: ParsedAuthor) -> OpenAlexSpider:
        return OpenAlexSpider(author_csv=str(make_csv_for_author(author)))

    return _make


@pytest.fixture
def make_spider_with_author_name() -> Callable[[ParsedAuthor], OpenAlexSpider]:
    def _make(author: ParsedAuthor) -> OpenAlexSpider:
        return OpenAlexSpider(author_name=author.normalized_name)

    return _make


@pytest.fixture
def make_publication_data(
    sample_authors: list[ParsedAuthor],
) -> Callable[[list[ParsedAuthor]], dict[str, Any]]:
    def _make(authors: list[ParsedAuthor]) -> dict[str, Any]:
        return {
            "id": "W4292663470",
            "title": "A Study on Testing",
            "publication_date": "2022-05-15",
            "primary_location": {"source": {"display_name": "Journal of Testing"}},
            "authorships": [{"author": {"display_name": author.full_name}} for author in authors],
            "doi": "https://doi.org/10.1234/test.doi",
        }

    return _make


class TestOpenAlexSpider:
    def test_no_arguments_raises_error(self) -> None:
        with pytest.raises(ValueError, match="requires either"):
            OpenAlexSpider()

    @pytest.mark.asyncio
    async def test_start_with_csv_file(
        self,
        make_spider_from_csv: Callable[[ParsedAuthor], OpenAlexSpider],
        sample_authors: list[ParsedAuthor],
    ) -> None:
        requests = []
        async for req in make_spider_from_csv(sample_authors[4]).start():
            requests.append(req)
        assert len(requests) == 1
        assert isinstance(requests[0], Request)
        query_params = parse_qs(urlparse(requests[0].url).query)
        assert query_params["search"] == [sample_authors[4].full_name]

    @pytest.mark.asyncio
    async def test_start_with_author_name(
        self,
        make_spider_with_author_name: Callable[[ParsedAuthor], OpenAlexSpider],
        sample_authors: list[ParsedAuthor],
    ) -> None:
        requests = []
        author = sample_authors[1]
        async for req in make_spider_with_author_name(author).start():
            requests.append(req)
        assert len(requests) == 1
        assert isinstance(requests[0], Request)
        parsed_url = urlparse(requests[0].url)
        query_params = parse_qs(parsed_url.query)
        assert query_params["search"] == [author.full_name]

    @pytest.mark.asyncio
    async def test_start_with_both_parameters(
        self,
        make_csv_for_author: Callable[[ParsedAuthor], Path],
        sample_authors: list[ParsedAuthor],
    ) -> None:
        csv_author = sample_authors[0]
        name_author = sample_authors[1]
        spider = OpenAlexSpider(
            author_csv=str(make_csv_for_author(csv_author)), author_name=name_author.full_name
        )
        requests = []
        async for req in spider.start():
            requests.append(req)
        assert len(requests) == 2
        assert all(isinstance(req, Request) for req in requests)
        query_params0 = parse_qs(urlparse(requests[0].url).query)
        assert query_params0["search"] == [csv_author.full_name]
        query_params1 = parse_qs(urlparse(requests[1].url).query)
        assert query_params1["search"] == [name_author.full_name]

    @pytest.mark.asyncio
    async def test_old_csv_format_still_supported(
        self, tmp_path: Path, sample_authors: list[ParsedAuthor]
    ) -> None:
        """CSVs with only FirstName and LastName columns are still supported."""
        csv_author = sample_authors[0]
        csv_path = tmp_path / "authors.csv"
        csv_path.write_text(
            f"Full Name,FirstName,LastName,Email\n"
            f"{csv_author.full_name},{csv_author.first_name},{csv_author.last_name},{csv_author.email}\n"
        )
        spider = OpenAlexSpider(author_csv=str(csv_path))
        requests = []
        async for req in spider.start():
            requests.append(req)
        assert len(requests) == 1
        assert isinstance(requests[0], Request)
        query_params = parse_qs(urlparse(requests[0].url).query)
        assert query_params["search"] == [f"{csv_author.first_name} {csv_author.last_name}"]

    def test_author_name_parameter(self, sample_authors: list[ParsedAuthor]) -> None:
        author = sample_authors[2]
        spider = OpenAlexSpider(author_name=author.full_name)
        assert spider.search_phrases == [author]

    def test_both_parameters_allowed(
        self,
        make_csv_for_author: Callable[[ParsedAuthor], Path],
        sample_authors: list[ParsedAuthor],
    ) -> None:
        csv_author = sample_authors[0]
        name_author = sample_authors[3]
        spider = OpenAlexSpider(
            author_csv=str(make_csv_for_author(csv_author)),
            author_name=name_author.full_name,
        )
        assert len(spider.search_phrases) == 2
        normalized_names = {record.normalized_name for record in spider.search_phrases}
        assert csv_author.normalized_name in normalized_names
        assert name_author.normalized_name in normalized_names

    def test_build_search_request(
        self,
        make_spider_with_author_name: Callable[[ParsedAuthor], OpenAlexSpider],
        sample_authors: list[ParsedAuthor],
    ) -> None:
        spider = make_spider_with_author_name(sample_authors[0])
        request = spider.build_search_request(sample_authors[0])
        assert isinstance(request, Request)
        assert request.url.startswith(spider.base_url + "/authors")
        query_params = parse_qs(urlparse(request.url).query)
        assert query_params["search"] == [sample_authors[0].full_name]
        assert "affiliations.institution.id:" in query_params["filter"][0]
        assert request.meta["matched_author"] == sample_authors[0].normalized_name

    @pytest.mark.parametrize(
        ("author_id", "cursor"),
        [
            ("A1234567890", "*"),
            ("A1234567890", "cursor123"),
        ],
    )
    def test_request_author_publications_with_pagination(
        self,
        make_spider_with_author_name: Callable[[ParsedAuthor], OpenAlexSpider],
        sample_authors: list[ParsedAuthor],
        author_id: str,
        cursor: str,
    ) -> None:
        author = sample_authors[0]
        spider = make_spider_with_author_name(author)
        requests = list(spider._request_author_publications(author_id, author.full_name, cursor))
        assert len(requests) == 1
        assert requests[0].url.startswith(spider.base_url + "/works")
        assert requests[0].cb_kwargs["author_id"] == author_id
        assert requests[0].meta["matched_author"] == author.full_name

        parsed_url = urlparse(requests[0].url)
        query_params = parse_qs(parsed_url.query)
        assert query_params["cursor"] == [cursor]

    def test_search_for_authors_skips_author_when_multiple_matches(
        self,
        make_spider_with_author_name: Callable[[ParsedAuthor], OpenAlexSpider],
        sample_authors: list[ParsedAuthor],
    ) -> None:
        author = sample_authors[0]
        spider = make_spider_with_author_name(author)
        response_data = {
            "results": [
                {"id": "https://openalex.org/A1234567890"},
                {"id": "https://openalex.org/A1234567891"},
            ]
        }
        request = spider.build_search_request(author)
        response = HtmlResponse(
            url=request.url,
            body=json.dumps(response_data),
            encoding="utf-8",
            request=request,
        )

        assert not list(spider._search_for_authors(response))

    def test_parse_publications_yields_items_and_pagination_request(
        self,
        make_spider_with_author_name: Callable[[ParsedAuthor], OpenAlexSpider],
        sample_authors: list[ParsedAuthor],
        make_publication_data: Callable[[list[ParsedAuthor]], dict[str, Any]],
    ) -> None:
        author = sample_authors[0]
        spider = make_spider_with_author_name(author)
        dummy_publication = make_publication_data([sample_authors[0], sample_authors[1]])
        publication_data = {
            "results": [
                {
                    **dummy_publication,
                },
                "invalid publication",
            ],
            "meta": {
                "cursor": "*",
                "next_cursor": "cursor123",
                "count": 2,
            },
        }
        initial_requests = list(
            spider._request_author_publications(
                author_id="A1234567890", matched_author=author.full_name, cursor="*"
            )
        )
        response = HtmlResponse(
            url=initial_requests[0].url,
            body=json.dumps(publication_data),
            encoding="utf-8",
            request=initial_requests[0],
        )

        emitted = list(spider._parse_publications(response, author_id="A1234567890"))
        assert len(emitted) == 2
        items = [x for x in emitted if isinstance(x, ArticleItem)]
        reqs = [x for x in emitted if isinstance(x, Request)]
        assert len(items) == 1
        assert len(reqs) == 1
        query = parse_qs(urlparse(reqs[0].url).query)
        assert query["cursor"] == ["cursor123"]

    def test_build_item(
        self,
        make_spider_with_author_name: Callable[[ParsedAuthor], OpenAlexSpider],
        sample_authors: list[ParsedAuthor],
        make_publication_data: Callable[[list[ParsedAuthor]], dict[str, Any]],
    ) -> None:
        author1 = sample_authors[0]
        author2 = sample_authors[1]
        publication_data = make_publication_data([author1, author2])
        spider = make_spider_with_author_name(author1)
        item = spider._build_article_item(publication_data, author1.normalized_name)
        assert isinstance(item, ArticleItem)
        assert item.doi == publication_data["doi"]
        assert item.title == publication_data["title"]
        assert (
            item.extra["journal_name"]
            == publication_data["primary_location"]["source"]["display_name"]
        )
        assert item.extra["matched_author"] == author1.normalized_name
        assert item.authors == ParsedAuthor.encode_author_string([author1, author2])

    @pytest.mark.parametrize(
        ("raw_type", "expected"),
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
        assert journal_name is None

        journal_name = OpenAlexSpider._extract_journal_name(
            {"primary_location": "invalid data", "locations": ["invalid location"]}
        )
        assert journal_name is None

    def test_extract_authors(
        self,
        sample_authors: list[ParsedAuthor],
        make_publication_data: Callable[[list[ParsedAuthor]], dict[str, Any]],
    ) -> None:
        dummy_publication = make_publication_data([sample_authors[0], sample_authors[1]])
        authors = OpenAlexSpider._extract_authors(dummy_publication)
        assert authors == [sample_authors[0], sample_authors[1]]

        authors = OpenAlexSpider._extract_authors({})
        assert authors == []
