import json
import pytest
from pathlib import Path
from scrapy.http import HtmlResponse, Request
from typing import Any
from urllib.parse import urlparse, parse_qs

from open_ire.items import OAPPublicationItem
from open_ire.spiders.oap_wos import OAPWoSSpider

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
                "identifiers": {
                    "identifier": [{"type": "doi", "value": "10.1000/sampledoi"}]
                }
            }
        },
    }

@pytest.fixture
def dummy_response(dummy_record: dict[str, Any]) -> HtmlResponse:
    json_body = {
        "Data": {
            "Records": {
                "records": {
                    "REC": [dummy_record]
                }
            }
        }
    }
    body_str = json.dumps(json_body)
    response = HtmlResponse(
        url="http://example.com/api",
        body=body_str,
        encoding="utf-8"
    )
    return response

@pytest.fixture
def dummy_spider(dummy_csv: Path, monkeypatch) -> OAPWoSSpider:
    monkeypatch.setenv("WOS_API_KEY", "dummy_api_key")
    return OAPWoSSpider(faculty_csv=str(dummy_csv), start_year="2020", end_year="2021")

class TestOAPWoSSpider:
    def test_build_item(self, dummy_spider: OAPWoSSpider, dummy_record: dict[str, Any]) -> None:
        item = dummy_spider._build_item(dummy_record)

        assert isinstance(item, OAPPublicationItem)
        assert item.title == "Sample Publication Title"
        assert item.publication_year == 2020
        assert item.doi == "10.1000/sampledoi"
        assert item.authors == "ElSayed, A, Doe, J"
        assert item.matched_author is None

    def test_parse_publications(self, dummy_spider: OAPWoSSpider, dummy_response: HtmlResponse) -> None:
        results = list(dummy_spider.parse_publications(dummy_response, page=1))

        items = [res for res in results if isinstance(res, OAPPublicationItem)]
        requests = [res for res in results if isinstance(res, Request)]

        assert len(items) == 1
        assert len(requests) == 0

        item = items[0]
        assert item.external_id == "WOS:000123456789"
        assert item.title == "Sample Publication Title"
        assert item.publication_year == 2020
        assert item.doi == "10.1000/sampledoi"
        assert item.authors == "ElSayed, A, Doe, J"
        assert item.matched_author is None

    def test_validate_year(self, dummy_spider: OAPWoSSpider) -> None:
        assert dummy_spider._validate_year("2020", "Some Field") == 2020
        with pytest.raises(ValueError):
            dummy_spider._validate_year("NotAYear", "Some Field")

    @pytest.mark.asyncio
    async def test_start(self, dummy_spider: OAPWoSSpider) -> None:
        requests = []
        async for req in dummy_spider.start():
            requests.append(req)

        assert len(requests) == 1
        assert requests[0].url.startswith(dummy_spider.base_url + "?count=25&databaseId=WOS")
