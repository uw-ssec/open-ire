import pytest
from scrapy.http import HtmlResponse

from open_ire.items import ArticleItem
from open_ire.settings import OPEN_IRE_SEARCH_TERMS
from open_ire.spiders.eric import EricSpider


def _detail_response(
    *,
    url: str = "https://eric.ed.gov/?id=EJ1234567",
    eric_number: str = "EJ1234567",
    eissn: str = "EISSN-1234-5678",
    authors: str = "Jane Doe; John Smith",
) -> HtmlResponse:
    """Build an ERIC detail-page response matching the selectors ``parse_detail`` reads."""
    html = f"""
    <div class="title">A Study of Something</div>
    <div><strong>ERIC Number:</strong> {eric_number}</div>
    <div><strong>Publication Date:</strong> 2025</div>
    <div><strong>EISSN:</strong> {eissn}</div>
    <div class="abstract">An abstract.</div>
    <div class="r_a"><div><div>{authors}</div></div></div>
    """
    return HtmlResponse(url=url, body=html.encode("utf-8"))


def _parse_one(response: HtmlResponse) -> ArticleItem:
    """Run ``parse_detail`` and return its single yielded item."""
    spider = EricSpider()
    (item,) = list(spider.parse_detail(response))
    return item


class TestEricSpider:
    def test_default_params(self) -> None:
        """Test spider initialization with default parameters."""
        spider = EricSpider()
        assert spider.name == "eric"
        assert len(spider.start_urls) == len(OPEN_IRE_SEARCH_TERMS)
        assert "eric.ed.gov" in spider.start_urls[0]
        assert "pg=1" in spider.start_urls[0]

    def test_custom_params(self) -> None:
        """Test spider initialization with custom parameters."""
        terms = "education,research"
        page = "2"
        spider = EricSpider(terms=terms, page=page)

        assert len(spider.start_urls) == 2
        assert "pg=2" in spider.start_urls[0]
        assert "q=education" in spider.start_urls[0]
        assert "q=research" in spider.start_urls[1]

    def test_extract_article_attribute(self) -> None:
        """Test the extract_article_attribute method."""
        html = """
        <div><strong>ERIC Number:</strong> EJ1234567</div>
        <div><strong>Publication Date:</strong> 2025</div>
        """
        response = HtmlResponse(url="https://eric.ed.gov", body=html.encode("utf-8"))

        eric_number = EricSpider.extract_article_attribute("ERIC Number", response)
        pub_date = EricSpider.extract_article_attribute("Publication Date", response)
        missing = EricSpider.extract_article_attribute("Missing Field", response)

        assert eric_number == "EJ1234567"
        assert pub_date == "2025"
        assert missing is None

    def test_parse_detail_strips_eissn_prefix(self) -> None:
        """A prefixed 'EISSN-<code>' page value is stored as the bare code (#116)."""
        item = _parse_one(_detail_response(eissn="EISSN-1234-5678"))
        assert item.eissn == "1234-5678"

    def test_parse_detail_stores_null_for_na_eissn(self) -> None:
        """The literal 'N/A' EISSN placeholder is stored as None, not a string (#116)."""
        item = _parse_one(_detail_response(eissn="N/A"))
        assert item.eissn is None

    def test_parse_detail_url_omits_search_query(self) -> None:
        """The stored URL links directly to the article, without the search query (#118)."""
        search_url = "https://eric.ed.gov/?q=university+of+washington&ft=on&pg=20&id=EJ1362008"
        item = _parse_one(_detail_response(url=search_url, eric_number="EJ1362008"))
        assert item.url == "https://eric.ed.gov/?id=EJ1362008"

    def test_parse_detail_normalizes_authors(self) -> None:
        """Raw author text is re-encoded as canonical 'Last, First' names (#102)."""
        item = _parse_one(_detail_response(authors="Jane Doe; John Smith"))
        assert item.authors == "Doe, Jane; Smith, John"

    def test_parse_detail_empty_authors_stored_as_none(self) -> None:
        """A page with no real authors (e.g. '; ') stores None, not a mangled string (#102)."""
        item = _parse_one(_detail_response(authors="; "))
        assert item.authors is None


@pytest.mark.parametrize("missing", ["title", "reference"])
def test_parse_detail_skips_missing_required_metadata(missing: str) -> None:
    title = '<div class="title">Example</div>'
    reference = "<div><strong>ERIC Number:</strong> EJ123</div>"
    html = reference if missing == "title" else title
    response = HtmlResponse(url="https://example.com/article", body=html.encode())
    assert list(EricSpider().parse_detail(response)) == []
