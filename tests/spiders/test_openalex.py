import csv
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from scrapy.http import HtmlResponse, Request

from open_ire.author import ParsedAuthor
from open_ire.enums import ArticleType
from open_ire.errors import AmbiguousAuthorError
from open_ire.items import ArticleItem
from open_ire.spiders.openalex import OpenAlexSpider


def _search_value(req: Request) -> str:
    return parse_qs(urlparse(req.url).query)["search"][0]


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
        return OpenAlexSpider(author_name=author.canonical_name)

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
        searched_authors = {record.canonical_name for record in spider.search_phrases}
        assert csv_author.canonical_name in searched_authors
        assert name_author.canonical_name in searched_authors

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
        assert request.meta["searched_author"] == sample_authors[0].canonical_name

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
        request = spider._build_publications_request(author_id, author.full_name, cursor)
        assert request.url.startswith(spider.base_url + "/works")
        assert request.cb_kwargs["author_id"] == author_id
        assert request.meta["searched_author"] == author.full_name

        parsed_url = urlparse(request.url)
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
        initial_request = spider._build_publications_request(
            author_id="A1234567890", searched_author=author.full_name, cursor="*"
        )
        response = HtmlResponse(
            url=initial_request.url,
            body=json.dumps(publication_data),
            encoding="utf-8",
            request=initial_request,
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
        item = spider._build_article_item(publication_data, author1.canonical_name)
        assert isinstance(item, ArticleItem)
        assert item.doi == publication_data["doi"]
        assert item.title == publication_data["title"]
        assert (
            item.extra["journal_name"]
            == publication_data["primary_location"]["source"]["display_name"]
        )
        assert item.extra["searched_author"] == author1.canonical_name
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


class TestAuthorDisambiguation:
    """Tests for author disambiguation based on recent institutional affiliation."""

    INSTITUTION_ID = "I201448701"

    @pytest.fixture
    def spider(self) -> OpenAlexSpider:
        return OpenAlexSpider(author_name="Test Author", start_date="2018-01-01")

    def _make_author(
        self,
        author_id: str,
        display_name: str,
        institution_id: str | None = None,
        years: list[int] | None = None,
    ) -> dict[str, Any]:
        author: dict[str, Any] = {"id": author_id, "display_name": display_name}
        if institution_id and years:
            author["affiliations"] = [
                {
                    "institution": {"id": f"https://openalex.org/{institution_id}"},
                    "years": years,
                }
            ]
        return author

    def test_disambiguate_single_affiliation(self, spider: OpenAlexSpider) -> None:
        # Only one author has affiliation with our institution
        authors = [
            self._make_author("A1", "Eunjung Kim", "I999999999", [2015, 2016]),
            self._make_author("A2", "Eunjung Kim", self.INSTITUTION_ID, [2020, 2021]),
            self._make_author("A3", "Eun-Jung Kim", "I888888888", [2022, 2023]),
        ]
        result = spider._disambiguate_authors(authors, "Eunjung Kim")
        assert isinstance(result, dict)
        assert result["id"] == "A2"

    def test_disambiguate_multiple_affiliations(self, spider: OpenAlexSpider) -> None:
        # Multiple authors have affiliation with our institution (ambiguous)
        authors = [
            self._make_author("A1", "Eunjung Kim", self.INSTITUTION_ID, [2015, 2016]),
            self._make_author("A2", "Eunjung Kim", self.INSTITUTION_ID, [2010, 2012]),
            self._make_author("A3", "Eun-Jung Kim", "I999999999", [2022, 2023]),
        ]
        with pytest.raises(AmbiguousAuthorError) as exc_info:
            spider._disambiguate_authors(authors, "Eunjung Kim")
        assert exc_info.value.author_name == "Eunjung Kim"
        # The candidate count is the number of affiliated authors (2)
        assert len(exc_info.value.candidates) == 2

    def test_disambiguate_no_affiliation(self, spider: OpenAlexSpider) -> None:
        # No authors have affiliation with our institution
        authors = [
            self._make_author("A1", "Eunjung Kim", "I999999999", [2020, 2021]),
            self._make_author("A2", "Eunjung Kim", "I888888888", [2019, 2022]),
            self._make_author("A3", "Eunjung Kim"),  # No affiliation at all
        ]
        with pytest.raises(AmbiguousAuthorError) as exc_info:
            spider._disambiguate_authors(authors, "Eunjung Kim")
        assert exc_info.value.author_name == "Eunjung Kim"
        # No affiliated authors
        assert len(exc_info.value.candidates) == 0

    def test_add_to_ambiguous_authors_stores_structured_records(
        self, spider: OpenAlexSpider
    ) -> None:
        authors = [
            self._make_author("A1", "Eunjung Kim", self.INSTITUTION_ID, [2015, 2016]),
            self._make_author("A2", "Eunjung Kim", self.INSTITUTION_ID, [2010, 2012]),
        ]

        spider._add_to_ambiguous_authors(
            searched_author="Eunjung Kim",
            author_records=authors,
            reason="no authors with recent institutional affiliation (>=2018)",
        )

        assert len(spider._ambiguous_authors) == 1
        ambiguous_author = spider._ambiguous_authors[0]
        assert ambiguous_author["searched_author"] == "Eunjung Kim"
        assert ambiguous_author["start_year"] == 2018
        assert ambiguous_author["candidates"] == authors

    @pytest.mark.parametrize(
        ("institution_a", "institution_b", "expected"),
        [
            (
                "https://openalex.org/I201448701",
                "https://openalex.org/I201448701",
                True,
            ),
            (
                "https://openalex.org/i201448701",
                "https://openalex.org/I201448701",
                True,
            ),
            (
                "https://openalex.org/I201448701",
                "https://openalex.org/I999999999",
                False,
            ),
            (None, "https://openalex.org/I201448701", False),
            (None, None, False),
            ("I201448701", "https://openalex.org/I201448701", True),
        ],
    )
    def test_same_institution(
        self, institution_a: str | None, institution_b: str | None, expected: bool
    ) -> None:
        assert OpenAlexSpider._same_institution(institution_a, institution_b) is expected

    def test_write_ambiguous_authors_file_creates_csv_with_expected_rows(
        self, spider: OpenAlexSpider, tmp_path: Path
    ) -> None:
        spider.ambiguous_authors_file = tmp_path / "ambiguous_authors.csv"

        ambiguous_authors = [
            self._make_author("https://openalex.org/A1", "Eunjung Kim"),
            self._make_author("https://openalex.org/A2", "Eunjung Kim"),
            self._make_author("https://openalex.org/A3", "Eunjung Kim"),
        ]
        spider._add_to_ambiguous_authors(
            searched_author="Eunjung Kim",
            author_records=ambiguous_authors,
            reason="no authors with recent institutional affiliation (>=2018)",
        )

        spider._write_ambiguous_authors_file()

        assert spider.ambiguous_authors_file.exists()
        with spider.ambiguous_authors_file.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        assert len(rows) == len(ambiguous_authors)
        assert spider._ambiguous_authors == []

    def test_extract_affiliations(self) -> None:
        author_record = {
            "affiliations": [
                {
                    "institution": {
                        "id": "https://openalex.org/I1234567890",
                        "display_name": "Test University",
                    },
                    "years": [2020, 2021],
                },
                "invalid",
                {"institution": "invalid"},
                {
                    "institution": {
                        "id": "https://openalex.org/I9999999999",
                        "display_name": "Other",
                    }
                },
            ]
        }

        affiliations = OpenAlexSpider._extract_affiliations(author_record)

        assert len(affiliations) == 2
        assert affiliations[0].institution.id == "https://openalex.org/I1234567890"
        assert affiliations[0].institution.name == "Test University"
        assert affiliations[0].years == [2020, 2021]
        assert affiliations[1].institution.id == "https://openalex.org/I9999999999"
        assert affiliations[1].institution.name == "Other"
        assert affiliations[1].years == []

    def test_extract_institution(self) -> None:
        record = {"id": "https://openalex.org/I1234567890", "display_name": "Test University"}
        institution = OpenAlexSpider._extract_institution(record)
        assert institution.id == "https://openalex.org/I1234567890"
        assert institution.name == "Test University"

        institution = OpenAlexSpider._extract_institution({})
        assert institution.id == ""
        assert institution.name == ""
