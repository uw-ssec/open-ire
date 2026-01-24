import copy
import json
import pytest
from pathlib import Path
from scrapy.http import HtmlResponse, Request
from typing import Any
from urllib.parse import urlparse, parse_qs

from open_ire.author import AuthorRecord
from open_ire.items import ArticleItem
from open_ire.spiders.wos import WoSSpider


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
def dummy_csv(tmp_path: Path, five_authors: list[AuthorRecord]) -> Path:
    csv_content = f"""Full Name,FirstName,LastName,Email
{five_authors[4].full_name},{five_authors[4].first_name},{five_authors[4].last_name},{five_authors[4].email}
"""
    csv_path = tmp_path / "dummy.csv"
    csv_path.write_text(csv_content)
    return csv_path


@pytest.fixture
def dummy_record(five_authors: list[AuthorRecord]) -> dict[str, Any]:
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
                        {"display_name": five_authors[4].normalized_name, "wos_standard": f"{five_authors[4].last_name}, {five_authors[4].first_initial}"},
                        {"display_name": five_authors[0].normalized_name, "wos_standard": f"{five_authors[0].last_name}, {five_authors[0].first_initial}"},
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


@pytest.fixture
def dummy_response(dummy_record: dict[str, Any]) -> HtmlResponse:
    json_body = {
        "Data": {"Records": {"records": {"REC": [dummy_record]}}},
        "QueryResult": {"RecordsFound": 1},
    }
    body_str = json.dumps(json_body)
    return HtmlResponse(url="http://example.com/api", body=body_str, encoding="utf-8")


@pytest.fixture
def spider(dummy_csv: Path, monkeypatch) -> WoSSpider:
    monkeypatch.setenv("WOS_API_KEY", "dummy_api_key")
    return WoSSpider(author_csv=str(dummy_csv), start_year="2020", end_year="2021")


class TestWoSSpider:
    def test_build_item(self, spider: WoSSpider, five_authors: list[AuthorRecord], dummy_record: dict[str, Any]) -> None:
        item = spider._build_item(dummy_record, five_authors[4].normalized_name)

        assert isinstance(item, ArticleItem)
        assert item.title == "Sample Publication Title"
        assert item.extra["publication_year"] == 2020
        assert item.extra["matched_author"] == five_authors[4].normalized_name
        assert item.doi == "10.1000/sampledoi"
        assert item.authors == AuthorRecord.encode_author_string([five_authors[4], five_authors[0]])
        assert item.url == "https://doi.org/10.1000/sampledoi"

    def test_build_item_without_doi(self, spider: WoSSpider, five_authors: list[AuthorRecord], dummy_record: dict[str, Any]) -> None:
        # Remove DOI from the record
        record_without_doi = copy.deepcopy(dummy_record)
        record_without_doi["dynamic_data"]["cluster_related"]["identifiers"] = {"identifier": []}

        item = spider._build_item(record_without_doi, five_authors[4].normalized_name)

        assert isinstance(item, ArticleItem)
        assert item.doi is None
        assert item.url == "https://www.webofscience.com/wos/woscc/full-record/WOS:000123456789"

    def test_build_item_missing_identifiers_section(self, spider: WoSSpider, five_authors: list[AuthorRecord], dummy_record: dict[str, Any]) -> None:
        # Remove entire identifiers section
        record_no_identifiers = copy.deepcopy(dummy_record)
        record_no_identifiers["dynamic_data"]["cluster_related"] = {}

        item = spider._build_item(record_no_identifiers, five_authors[4].normalized_name)

        assert isinstance(item, ArticleItem)
        assert item.doi is None
        assert item.url == "https://www.webofscience.com/wos/woscc/full-record/WOS:000123456789"

    def test_parse_publications(self, spider: WoSSpider, five_authors: list[AuthorRecord], dummy_response: HtmlResponse) -> None:
        query = spider._build_query(five_authors[4].normalized_name)
        # Create a request with meta to tie to the response
        request = Request(url="http://example.com/api", meta={'matched_author': five_authors[4].normalized_name})
        dummy_response = dummy_response.replace(request=request)

        results = list(spider.parse_publications(dummy_response, query, page=1))
        items = [res for res in results if isinstance(res, ArticleItem)]
        requests = [res for res in results if isinstance(res, Request)]

        assert len(items) == 1
        assert len(requests) == 0

        item = items[0]
        assert item.reference == "WOS:000123456789"
        assert item.title == "Sample Publication Title"
        assert item.extra["publication_year"] == 2020
        assert item.extra["matched_author"] == five_authors[4].normalized_name
        assert item.doi == "10.1000/sampledoi"
        assert item.authors == AuthorRecord.encode_author_string([five_authors[4], five_authors[0]])

    def test_validate_year(self, spider: WoSSpider) -> None:
        assert spider._validate_year("2020", "Some Field") == 2020
        with pytest.raises(ValueError):
            spider._validate_year("NotAYear", "Some Field")

    def test_build_search_request(self, spider: WoSSpider, five_authors: list[AuthorRecord]) -> None:
        request = spider.build_search_request(five_authors[4].normalized_name)

        assert request.url.startswith(spider.base_url + "?count=25&databaseId=WOS")

    def test_parse_publications_no_results_records_empty_string(
        self, spider: WoSSpider, five_authors: list[AuthorRecord]
    ) -> None:
        # WoS empty results schema: records is an empty string
        json_body = {
            "Data": {"Records": {"records": ""}},
            "QueryResult": {"RecordsFound": 0},
        }
        body_str = json.dumps(json_body)
        request = Request(url="http://example.com/api", meta={'matched_author': five_authors[1].normalized_name})
        response = HtmlResponse(url="http://example.com/api", body=body_str, encoding="utf-8", request=request)

        query = spider._build_query(five_authors[1].normalized_name)
        results = list(spider.parse_publications(response, query, page=1))

        items = [res for res in results if isinstance(res, ArticleItem)]
        requests = [res for res in results if isinstance(res, Request)]

        assert items == []
        assert requests == []

    def test_parse_publications_records_container_string_is_ignored(
        self, spider: WoSSpider, five_authors: list[AuthorRecord]
    ) -> None:
        # Sometimes WoS returns a string in `records` (e.g., error-ish message).
        json_body = {
            "Data": {"Records": {"records": "No results found"}},
            "QueryResult": {"RecordsFound": 0},
        }
        body_str = json.dumps(json_body)
        request = Request(url="http://example.com/api", meta={'matched_author': five_authors[2].normalized_name})
        response = HtmlResponse(url="http://example.com/api", body=body_str, encoding="utf-8", request=request)

        query = spider._build_query(five_authors[2].normalized_name)
        results = list(spider.parse_publications(response, query, page=1))

        items = [res for res in results if isinstance(res, ArticleItem)]
        requests = [res for res in results if isinstance(res, Request)]

        assert items == []
        assert requests == []
