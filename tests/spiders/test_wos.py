import json
import pytest
from pathlib import Path
from scrapy.http import HtmlResponse, Request
from typing import Any
from urllib.parse import urlparse, parse_qs

from open_ire.items import ArticleItem
from open_ire.spiders.wos import WoSSpider


@pytest.fixture
def dummy_csv(tmp_path: Path) -> Path:
    csv_content = """Full Name,FirstName,LastName,Email
Amina ElSayed,Amina,ElSayed,amina.elsayed@example.edu
"""
    csv_path = tmp_path / "dummy.csv"
    csv_path.write_text(csv_content)
    return csv_path


@pytest.fixture
def dummy_record() -> dict[str, Any]:
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
                        {"display_name": "ElSayed, Amina", "wos_standard": "ElSayed, A"},
                        {"display_name": "Doe, John", "wos_standard": "Doe, J"},
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
    json_body = {"Data": {"Records": {"records": {"REC": [dummy_record]}}}}
    body_str = json.dumps(json_body)
    response = HtmlResponse(url="http://example.com/api", body=body_str, encoding="utf-8")
    return response


@pytest.fixture
def spider(dummy_csv: Path, monkeypatch) -> WoSSpider:
    monkeypatch.setenv("WOS_API_KEY", "dummy_api_key")
    return WoSSpider(faculty_csv=str(dummy_csv), start_year="2020", end_year="2021")


class TestWoSSpider:
    def test_build_item(self, spider: WoSSpider, dummy_record: dict[str, Any]) -> None:
        item = spider._build_item(dummy_record)

        assert isinstance(item, ArticleItem)
        assert item.title == "Sample Publication Title"
        assert item.extra["publication_year"] == 2020
        assert item.doi == "10.1000/sampledoi"
        assert item.authors == "ElSayed, A, Doe, J"
        assert item.extra["matched_author"] is None

    def test_parse_publications(self, spider: WoSSpider, dummy_response: HtmlResponse) -> None:
        query = spider._build_query("Kemi Adeyemi")
        results = list(spider.parse_publications(dummy_response, query, page=1))
        items = [res for res in results if isinstance(res, ArticleItem)]
        requests = [res for res in results if isinstance(res, Request)]

        assert len(items) == 1
        assert len(requests) == 0

        item = items[0]
        assert item.reference == "WOS:000123456789"
        assert item.title == "Sample Publication Title"
        assert item.extra["publication_year"] == 2020
        assert item.doi == "10.1000/sampledoi"
        assert item.authors == "ElSayed, A, Doe, J"
        assert item.extra["matched_author"] is None

    def test_validate_year(self, spider: WoSSpider) -> None:
        assert spider._validate_year("2020", "Some Field") == 2020
        with pytest.raises(ValueError):
            spider._validate_year("NotAYear", "Some Field")

    def test_build_search_request(self, spider: WoSSpider) -> None:
        request = spider.build_search_request("Adeyemi Kemi")

        assert request.url.startswith(spider.base_url + "?count=25&databaseId=WOS")
