import pytest
from scrapy.http import HtmlResponse

from open_ire.items import ArticleItem
from open_ire.settings import OPEN_IRE_SEARCH_TERMS
from open_ire.spiders.epa import EPASpider


class TestEPASpider:
    def test_default_params(self) -> None:
        """Test spider initialization with default parameters."""
        spider = EPASpider()
        assert spider.name == "epa"
        assert len(spider.start_urls) == len(OPEN_IRE_SEARCH_TERMS)
        assert "cfpub.epa.gov" in spider.start_urls[0]
        assert "count=25" in spider.start_urls[0]
        assert "startIndex" not in spider.start_urls[0]

    def test_custom_params(self) -> None:
        """Test spider initialization with custom parameters."""
        terms = "education,research"
        page = "3"
        spider = EPASpider(terms=terms, page=page)

        assert spider.target_page == 3
        assert len(spider.start_urls) == 2
        assert "searchall=education" in spider.start_urls[0]
        assert "searchall=research" in spider.start_urls[1]
        assert "count=25" in spider.start_urls[0]
        assert "startIndex=51" in spider.start_urls[0]

    def test_extract_file_urls(self) -> None:
        """Test the extract_file_urls method."""
        html = """
        <div>
            <a href="si_public_file_download.cfm?p_download_id=1">File 1</a>
            <a href="si_public_file_download.cfm?p_download_id=2">File 2</a>
            <a href="other.html">Other link</a>
        </div>
        """
        response = HtmlResponse(url="https://cfpub.epa.gov/si/", body=html.encode("utf-8"))

        spider = EPASpider(terms="test", page="1")
        urls = spider.extract_file_urls(response)

        assert len(urls) == 2
        assert "https://cfpub.epa.gov/si/si_public_file_download.cfm?p_download_id=1" in urls
        assert "https://cfpub.epa.gov/si/si_public_file_download.cfm?p_download_id=2" in urls

    def test_extract_authors(self) -> None:
        """Test the extract_authors method."""
        expected_title = "Sample Title"
        expected_author = "Author, A. B."
        html = f"""
        <div>
            <h2>Citation:</h2>
            <p>{expected_author} {expected_title}. Unittest (2025).</p>
        </div>
        """
        response = HtmlResponse(url="https://cfpub.epa.gov/si/", body=html.encode("utf-8"))

        author = EPASpider.extract_authors(response, expected_title)
        assert author == expected_author


@pytest.mark.parametrize("missing", ["title", "reference"])
def test_parse_detail_skips_missing_required_metadata(missing: str) -> None:
    title = '<meta name="DC.title" content="Example">'
    reference = '<span id="recordID">123</span>'
    html = reference if missing == "title" else title
    response = HtmlResponse(url="https://example.com/article", body=html.encode())
    assert list(EPASpider().parse_detail(response)) == []


def test_parse_detail_extracts_article() -> None:
    html = """
    <meta name="DC.title" content="Example">
    <span id="recordID">123</span>
    <a href="si_public_file_download.cfm?p_download_id=1">PDF</a>
    """
    response = HtmlResponse(url="https://cfpub.epa.gov/si/", body=html.encode())
    results = list(EPASpider().parse_detail(response))
    assert len(results) == 1
    item = results[0]
    assert isinstance(item, ArticleItem)
    assert item.reference == "123"
    assert item.file_urls == [
        "https://cfpub.epa.gov/si/si_public_file_download.cfm?p_download_id=1"
    ]
