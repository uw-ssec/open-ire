import json
from collections.abc import Generator
from datetime import date
from typing import Any
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from scrapy.http import Request, TextResponse, XmlResponse

from open_ire.items import ArticleItem
from open_ire.settings import OPEN_IRE_SEARCH_TERMS
from open_ire.spiders.pmc import PMCSpider


@pytest.fixture
def spider() -> Generator[PMCSpider, None, None]:
    with patch.object(PMCSpider, "logger", new_callable=MagicMock):
        yield PMCSpider(terms="university of washington")


def _json_response(url: str, payload: dict[str, Any], meta: dict[str, Any]) -> TextResponse:
    request = Request(url, meta=meta)
    return TextResponse(
        url=url,
        body=json.dumps(payload).encode("utf-8"),
        encoding="utf-8",
        request=request,
    )


def _s3_response(body: str, item: ArticleItem) -> XmlResponse:
    request = Request(
        "https://pmc-oa-opendata.s3.amazonaws.com/?list-type=2&prefix=" + item.reference,
        cb_kwargs={"item": item},
    )
    return XmlResponse(
        url=request.url, body=body.encode("utf-8"), encoding="utf-8", request=request
    )


def _s3_listing(keys: list[str]) -> str:
    """Build an S3 ListBucketResult XML body (with the S3 default namespace)."""
    contents = "".join(f"<Contents><Key>{key}</Key></Contents>" for key in keys)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        f"<Name>pmc-oa-opendata</Name>{contents}</ListBucketResult>"
    )


# Field names and shapes mirror a live NCBI PMC esummary (JSON, version 2.0)
# record: no issn/essn, dates exposed as sortdate/epubdate/pubdate.
SAMPLE_RECORD: dict[str, Any] = {
    "uid": "9876543",
    "title": "Ocean Acidification in the Salish Sea",
    "sortdate": "2024/03/15 00:00",
    "epubdate": "2024 Mar 15",
    "pubdate": "2024 Mar",
    "source": "Mar Biol",
    "fulljournalname": "Marine Biology",
    "volume": "12",
    "issue": "3",
    "pages": "100-110",
    "authors": [
        {"name": "Habell-Pallán M", "authtype": "Author"},
        {"name": "Smith JA", "authtype": "Author"},
    ],
    "articleids": [
        {"idtype": "pmid", "value": "33445566"},
        {"idtype": "pmcid", "value": "PMC9876543"},
        {"idtype": "doi", "value": "10.1234/marbio.2024.99"},
    ],
}


class TestInit:
    def test_default_terms(self) -> None:
        spider = PMCSpider()
        assert spider.name == "pmc"
        assert spider.search_phrases == list(OPEN_IRE_SEARCH_TERMS)

    def test_custom_terms(self) -> None:
        spider = PMCSpider(terms="coral bleaching, kelp forests")
        assert spider.search_phrases == ["coral bleaching", "kelp forests"]

    def test_api_key_absent_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NCBI_API_KEY", raising=False)
        assert PMCSpider().api_key is None

    def test_api_key_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NCBI_API_KEY", "secret-key")
        assert PMCSpider().api_key == "secret-key"


class TestRequestBuilding:
    def test_build_search_request_targets_esearch(self, spider: PMCSpider) -> None:
        request = spider.build_search_request("university of washington")
        parsed = urlparse(request.url)
        query = parse_qs(parsed.query)

        assert parsed.path.endswith("/esearch.fcgi")
        assert request.meta == {"search_term": "university of washington", "retstart": 0}
        assert query["db"] == ["pmc"]
        assert query["retmode"] == ["json"]
        assert query["retmax"] == ["100"]
        assert query["retstart"] == ["0"]
        assert query["email"][0]
        assert query["tool"] == ["open_ire"]

    def test_query_restricts_to_oa_affiliation(self, spider: PMCSpider) -> None:
        request = spider.build_search_request("university of washington")
        term = parse_qs(urlparse(request.url).query)["term"][0]
        assert term == '"university of washington"[Affiliation] AND open access[filter]'

    def test_api_key_included_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NCBI_API_KEY", "secret-key")
        spider = PMCSpider(terms="x")
        request = spider.build_search_request("x")
        assert parse_qs(urlparse(request.url).query)["api_key"] == ["secret-key"]

    def test_esummary_request_batches_uids(self, spider: PMCSpider) -> None:
        request = spider._build_esummary_request("term", ["111", "222", "333"])
        parsed = urlparse(request.url)
        query = parse_qs(parsed.query)

        assert parsed.path.endswith("/esummary.fcgi")
        assert query["id"] == ["111,222,333"]
        assert request.callback == spider.parse_summary
        assert request.meta == {"search_term": "term"}


class TestParseEsearch:
    def test_yields_summary_request_and_pagination(self, spider: PMCSpider) -> None:
        payload = {"esearchresult": {"count": "250", "idlist": ["1", "2", "3"]}}
        response = _json_response(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
            payload,
            meta={"search_term": "uw", "retstart": 0},
        )

        outputs = list(spider.parse(response))

        assert len(outputs) == 2
        summary_request, next_page = outputs
        assert summary_request.callback == spider.parse_summary
        assert parse_qs(urlparse(summary_request.url).query)["id"] == ["1,2,3"]
        assert next_page.callback == spider.parse
        assert next_page.meta["retstart"] == 100

    def test_stops_pagination_on_last_page(self, spider: PMCSpider) -> None:
        payload = {"esearchresult": {"count": "2", "idlist": ["1", "2"]}}
        response = _json_response(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
            payload,
            meta={"search_term": "uw", "retstart": 0},
        )

        outputs = list(spider.parse(response))

        assert len(outputs) == 1
        assert outputs[0].callback == spider.parse_summary

    def test_no_results(self, spider: PMCSpider) -> None:
        payload = {"esearchresult": {"count": "0", "idlist": []}}
        response = _json_response(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
            payload,
            meta={"search_term": "uw", "retstart": 0},
        )

        assert list(spider.parse(response)) == []

    def test_invalid_json(self, spider: PMCSpider) -> None:
        request = Request(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
            meta={"search_term": "uw", "retstart": 0},
        )
        response = TextResponse(
            url=request.url, body=b"<not json>", encoding="utf-8", request=request
        )
        assert list(spider.parse(response)) == []


class TestParseSummary:
    def _summary_response(self, records: dict[str, Any]) -> TextResponse:
        payload = {"result": {"uids": list(records.keys()), **records}}
        return _json_response(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
            payload,
            meta={"search_term": "uw"},
        )

    def _single_item(self, spider: PMCSpider, record: dict[str, Any], uid: str) -> ArticleItem:
        requests = list(spider.parse_summary(self._summary_response({uid: record})))
        item: ArticleItem = next(iter(requests)).cb_kwargs["item"]
        return item

    def test_yields_oa_request_carrying_item(self, spider: PMCSpider) -> None:
        response = self._summary_response({"9876543": SAMPLE_RECORD})

        outputs = list(spider.parse_summary(response))

        assert len(outputs) == 1
        request = outputs[0]
        assert request.callback == spider.parse_oa_listing
        assert request.errback == spider.handle_oa_error
        query = parse_qs(urlparse(request.url).query)
        assert "s3.amazonaws.com" in request.url
        assert query["prefix"] == ["PMC9876543"]

        item = request.cb_kwargs["item"]
        assert isinstance(item, ArticleItem)
        assert item.title == "Ocean Acidification in the Salish Sea"
        assert item.reference == "PMC9876543"
        assert item.repository == "pmc"
        assert item.url == "https://pmc.ncbi.nlm.nih.gov/articles/PMC9876543/"
        assert item.file_urls == []
        assert item.doi == "10.1234/marbio.2024.99"
        assert item.publication_date == date(2024, 3, 15)
        assert item.authors == "Habell-Pallán, M; Smith, JA"
        assert item.extra == {
            "journal_name": "Marine Biology",
            "volume": "12",
            "issue": "3",
            "pages": "100-110",
            "pmid": "33445566",
        }

    def test_falls_back_to_pubdate(self, spider: PMCSpider) -> None:
        record = {**SAMPLE_RECORD}
        del record["sortdate"]
        record["epubdate"] = ""
        record["pubdate"] = "2022 Jul 04"
        item = self._single_item(spider, record, "9876543")
        assert item.publication_date == date(2022, 7, 4)

    def test_skips_record_missing_title(self, spider: PMCSpider) -> None:
        record = {**SAMPLE_RECORD, "title": ""}
        assert list(spider.parse_summary(self._summary_response({"9876543": record}))) == []

    def test_derives_pmcid_from_uid(self, spider: PMCSpider) -> None:
        record = {"uid": "55555", "title": "No articleids here", "articleids": []}
        item = self._single_item(spider, record, "55555")
        assert item.reference == "PMC55555"

    def test_skips_non_dict_entries(self, spider: PMCSpider) -> None:
        payload = {"result": {"uids": ["1"], "1": "not-a-dict"}}
        response = _json_response(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
            payload,
            meta={"search_term": "uw"},
        )
        assert list(spider.parse_summary(response)) == []


class TestParseOAListing:
    def _item(self, reference: str = "PMC9876543") -> ArticleItem:
        return ArticleItem(
            reference=reference,
            repository="pmc",
            title="Ocean Acidification in the Salish Sea",
            url=f"https://pmc.ncbi.nlm.nih.gov/articles/{reference}/",
        )

    def test_attaches_main_pdf_link(self, spider: PMCSpider) -> None:
        item = self._item()
        listing = _s3_listing(
            [
                "PMC9876543.1/PMC9876543.1.json",
                "PMC9876543.1/PMC9876543.1.pdf",
                "PMC9876543.1/supplement.pdf",
                "PMC9876543.1/fig01.jpg",
            ]
        )
        outputs = list(spider.parse_oa_listing(_s3_response(listing, item), item=item))

        assert outputs == [item]
        assert item.file_urls == [
            "https://pmc-oa-opendata.s3.amazonaws.com/PMC9876543.1/PMC9876543.1.pdf"
        ]

    def test_prefers_highest_version(self, spider: PMCSpider) -> None:
        item = self._item()
        listing = _s3_listing(["PMC9876543.1/PMC9876543.1.pdf", "PMC9876543.2/PMC9876543.2.pdf"])
        list(spider.parse_oa_listing(_s3_response(listing, item), item=item))
        assert item.file_urls == [
            "https://pmc-oa-opendata.s3.amazonaws.com/PMC9876543.2/PMC9876543.2.pdf"
        ]

    def test_ignores_neighbouring_pmcid_prefix(self, spider: PMCSpider) -> None:
        item = self._item(reference="PMC123")
        # A prefix query for "PMC123" also returns "PMC1230"; only the exact id wins.
        listing = _s3_listing(["PMC1230.1/PMC1230.1.pdf", "PMC123.1/PMC123.1.pdf"])
        list(spider.parse_oa_listing(_s3_response(listing, item), item=item))
        assert item.file_urls == ["https://pmc-oa-opendata.s3.amazonaws.com/PMC123.1/PMC123.1.pdf"]

    def test_no_pdf_yields_item_without_file(self, spider: PMCSpider) -> None:
        item = self._item()
        listing = _s3_listing(["PMC9876543.1/PMC9876543.1.xml"])
        outputs = list(spider.parse_oa_listing(_s3_response(listing, item), item=item))

        assert outputs == [item]
        assert item.file_urls == []

    def test_empty_listing_yields_item_without_file(self, spider: PMCSpider) -> None:
        item = self._item()
        outputs = list(spider.parse_oa_listing(_s3_response(_s3_listing([]), item), item=item))

        assert outputs == [item]
        assert item.file_urls == []

    def test_oa_lookup_request_url(self, spider: PMCSpider) -> None:
        request = spider._build_oa_lookup_request(self._item())
        query = parse_qs(urlparse(request.url).query)
        assert request.url.startswith("https://pmc-oa-opendata.s3.amazonaws.com/?")
        assert query["prefix"] == ["PMC9876543"]
        assert query["list-type"] == ["2"]

    def test_select_pdf_key_returns_none_without_match(self) -> None:
        assert PMCSpider._select_pdf_key("PMC9876543", []) is None
        assert PMCSpider._select_pdf_key("PMC9876543", ["PMC9876543.1/other.pdf"]) is None

    def test_handle_oa_error_yields_item(self, spider: PMCSpider) -> None:
        item = self._item()
        failure = MagicMock()
        failure.request.cb_kwargs = {"item": item}
        assert list(spider.handle_oa_error(failure)) == [item]
        assert item.file_urls == []


class TestHelpers:
    def test_extract_pmcid_strips_version(self) -> None:
        record = {"articleids": [{"idtype": "pmcid", "value": "PMC123.1"}]}
        assert PMCSpider._extract_pmcid(record) == "PMC123"

    def test_format_author_name(self) -> None:
        assert PMCSpider._format_author_name("Smith JA") == "Smith, JA"
        assert PMCSpider._format_author_name("van der Berg J") == "van der Berg, J"
        assert PMCSpider._format_author_name("Madonna") == "Madonna"

    def test_extract_authors_empty(self) -> None:
        assert PMCSpider._extract_authors({"authors": []}) is None
        assert PMCSpider._extract_authors({}) is None
