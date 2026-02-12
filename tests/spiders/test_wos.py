import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from scrapy.http import HtmlResponse, Request

from open_ire.author import ParsedAuthor
from open_ire.enums import ArticleType
from open_ire.items import ArticleItem
from open_ire.spiders.wos import WoSSpider


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
            f"{author.full_name},{author.first_name},{author.middle_names},{author.last_name},{author.email}\n"
        )
        return csv_path

    return _make


@pytest.fixture
def make_spider_from_csv(
    make_csv_for_author: Callable[[ParsedAuthor], Path],
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[ParsedAuthor], WoSSpider]:
    def _make(author: ParsedAuthor) -> WoSSpider:
        monkeypatch.setenv("WOS_API_KEY", "dummy_api_key")
        return WoSSpider(
            author_csv=str(make_csv_for_author(author)), start_year="2020", end_year="2021"
        )

    return _make


@pytest.fixture
def make_spider_with_author_name(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[ParsedAuthor], WoSSpider]:
    def _make(author: ParsedAuthor) -> WoSSpider:
        monkeypatch.setenv("WOS_API_KEY", "dummy_api_key")
        return WoSSpider(author_name=author.normalized_name, start_year="2020", end_year="2021")

    return _make


@pytest.fixture
def make_record_data() -> Callable[[list[ParsedAuthor]], dict[str, Any]]:
    def _make(authors: list[ParsedAuthor]) -> dict[str, Any]:
        return {
            "UID": "WOS:000123456789",
            "static_data": {
                "summary": {
                    "pub_info": {
                        "pubyear": 2020,
                        "coverdate": "JAN 2020",
                    },
                    "titles": {
                        "title": [
                            {"type": "item", "content": "Sample Publication Title"},
                            {"type": "source", "content": "Journal of Testing"},
                        ]
                    },
                    "names": {
                        "name": [
                            {
                                "display_name": author.normalized_name,
                                "wos_standard": f"{author.last_name}, {author.first_initial}",
                            }
                            for author in authors
                        ]
                    },
                    "doctypes": {"doctype": "Article"},
                }
            },
            "dynamic_data": {
                "cluster_related": {
                    "identifiers": {"identifier": [{"type": "doi", "value": "10.1000/sampledoi"}]}
                }
            },
        }

    return _make


class TestWoSSpider:
    def test_build_item(
        self,
        make_spider_with_author_name: Callable[[ParsedAuthor], WoSSpider],
        sample_authors: list[ParsedAuthor],
        make_record_data: Callable[[list[ParsedAuthor]], dict[str, Any]],
    ) -> None:
        author = sample_authors[4]
        spider = make_spider_with_author_name(author)
        record = make_record_data([sample_authors[4], sample_authors[0]])
        item = spider._build_item(record, author.normalized_name)

        assert isinstance(item, ArticleItem)
        assert item.title == "Sample Publication Title"
        assert item.extra["matched_author"] == author.normalized_name
        assert item.doi == "10.1000/sampledoi"
        assert item.authors == ParsedAuthor.encode_author_string(
            [sample_authors[4], sample_authors[0]]
        )
        assert item.url == "https://doi.org/10.1000/sampledoi"

    def test_build_item_without_doi(
        self,
        make_spider_with_author_name: Callable[[ParsedAuthor], WoSSpider],
        sample_authors: list[ParsedAuthor],
        make_record_data: Callable[[list[ParsedAuthor]], dict[str, Any]],
    ) -> None:
        author = sample_authors[4]
        spider = make_spider_with_author_name(author)
        record = make_record_data([author])
        record["dynamic_data"]["cluster_related"]["identifiers"] = {"identifier": []}

        item = spider._build_item(record, author.normalized_name)

        assert isinstance(item, ArticleItem)
        assert item.doi is None
        assert item.url == "https://www.webofscience.com/wos/woscc/full-record/WOS:000123456789"

    def test_build_item_missing_identifiers_section(
        self,
        make_spider_with_author_name: Callable[[ParsedAuthor], WoSSpider],
        sample_authors: list[ParsedAuthor],
        make_record_data: Callable[[list[ParsedAuthor]], dict[str, Any]],
    ) -> None:
        author = sample_authors[4]
        spider = make_spider_with_author_name(author)
        record = make_record_data([author])
        record["dynamic_data"]["cluster_related"] = {}

        item = spider._build_item(record, author.normalized_name)

        assert isinstance(item, ArticleItem)
        assert item.doi is None
        assert item.url == "https://www.webofscience.com/wos/woscc/full-record/WOS:000123456789"

    def test_parse_publications(
        self,
        make_spider_with_author_name: Callable[[ParsedAuthor], WoSSpider],
        sample_authors: list[ParsedAuthor],
        make_record_data: Callable[[list[ParsedAuthor]], dict[str, Any]],
    ) -> None:
        author = sample_authors[4]
        spider = make_spider_with_author_name(author)
        record = make_record_data([sample_authors[4], sample_authors[0]])
        json_body = {
            "Data": {"Records": {"records": {"REC": [record]}}},
            "QueryResult": {"RecordsFound": 1},
        }
        query = spider._build_query(spider.author_name_for_query(sample_authors[4]))
        request = Request(
            url="http://example.com/api", meta={"matched_author": author.normalized_name}
        )
        response = HtmlResponse(
            url="http://example.com/api",
            body=json.dumps(json_body),
            encoding="utf-8",
            request=request,
        )

        results = list(spider.parse_publications(response, query, page=1))
        items = [res for res in results if isinstance(res, ArticleItem)]
        requests = [res for res in results if isinstance(res, Request)]

        assert len(items) == 1
        assert len(requests) == 0

        item = items[0]
        assert item.reference == "WOS:000123456789"
        assert item.title == "Sample Publication Title"
        assert item.extra["matched_author"] == author.normalized_name
        assert item.doi == "10.1000/sampledoi"
        assert item.authors == ParsedAuthor.encode_author_string(
            [sample_authors[4], sample_authors[0]]
        )

    def test_build_search_request(
        self,
        make_spider_with_author_name: Callable[[ParsedAuthor], WoSSpider],
        sample_authors: list[ParsedAuthor],
    ) -> None:
        author = sample_authors[4]
        spider = make_spider_with_author_name(author)
        request = spider.build_search_request(author)

        assert request.url.startswith(spider.base_url + "?count=25&databaseId=WOS")
        assert request.meta["matched_author"] == sample_authors[4].normalized_name

    def test_build_search_request_with_author_name_normalizes_to_wos_format(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("WOS_API_KEY", "dummy_api_key")
        spider = WoSSpider(author_name="John Doe")

        request = spider.build_search_request(spider.search_phrases[0])

        assert spider.search_phrases == [ParsedAuthor("John Doe")]
        assert 'AU=("Doe, John")' in request.cb_kwargs["query"]
        assert request.meta["matched_author"] == "Doe, John"

    def test_parse_publications_no_results_records_empty_string(
        self,
        make_spider_with_author_name: Callable[[ParsedAuthor], WoSSpider],
        sample_authors: list[ParsedAuthor],
    ) -> None:
        author = sample_authors[1]
        spider = make_spider_with_author_name(author)
        json_body = {
            "Data": {"Records": {"records": ""}},
            "QueryResult": {"RecordsFound": 0},
        }
        request = Request(
            url="http://example.com/api", meta={"matched_author": author.normalized_name}
        )
        response = HtmlResponse(
            url="http://example.com/api",
            body=json.dumps(json_body),
            encoding="utf-8",
            request=request,
        )

        query = spider._build_query(spider.author_name_for_query(author))
        results = list(spider.parse_publications(response, query, page=1))

        items = [res for res in results if isinstance(res, ArticleItem)]
        requests = [res for res in results if isinstance(res, Request)]

        assert items == []
        assert requests == []

    def test_parse_publications_records_container_string_is_ignored(
        self,
        make_spider_with_author_name: Callable[[ParsedAuthor], WoSSpider],
        sample_authors: list[ParsedAuthor],
    ) -> None:
        author = sample_authors[2]
        spider = make_spider_with_author_name(author)
        json_body = {
            "Data": {"Records": {"records": "No results found"}},
            "QueryResult": {"RecordsFound": 0},
        }
        request = Request(
            url="http://example.com/api", meta={"matched_author": author.normalized_name}
        )
        response = HtmlResponse(
            url="http://example.com/api",
            body=json.dumps(json_body),
            encoding="utf-8",
            request=request,
        )

        query = spider._build_query(spider.author_name_for_query(author))
        results = list(spider.parse_publications(response, query, page=1))

        items = [res for res in results if isinstance(res, ArticleItem)]
        requests = [res for res in results if isinstance(res, Request)]

        assert items == []
        assert requests == []

    @pytest.mark.parametrize(
        ("raw_type", "expected"),
        [
            ("article", ArticleType.SCHOLARLY_ARTICLE),
            ("Article", ArticleType.SCHOLARLY_ARTICLE),
            ("proceedings paper", ArticleType.SCHOLARLY_ARTICLE),
            ("Proceedings Paper", ArticleType.SCHOLARLY_ARTICLE),
            ("review", ArticleType.SCHOLARLY_ARTICLE),
            ("book review", ArticleType.SCHOLARLY_ARTICLE),
            ("Book Review", ArticleType.SCHOLARLY_ARTICLE),
            ("editorial material", ArticleType.OTHER),
            ("Editorial Material", ArticleType.OTHER),
            ("letter", ArticleType.OTHER),
            ("Letter", ArticleType.OTHER),
        ],
    )
    def test_normalize_type_known_types(self, raw_type: str, expected: ArticleType) -> None:
        assert WoSSpider._normalize_type(raw_type) == expected

    def test_normalize_type_none(self) -> None:
        assert WoSSpider._normalize_type(None) is None

    def test_normalize_type_unknown(self) -> None:
        assert WoSSpider._normalize_type("unknown-type") is None
