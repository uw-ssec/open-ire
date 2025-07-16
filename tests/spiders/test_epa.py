import pytest
from scrapy.http import HtmlResponse
from scrapy.utils.test import get_crawler

from open_ire.spiders.epa import EPASpider


class TestEPASpider:
    @pytest.fixture
    def spider(self):
        """Create a spider instance for testing."""
        crawler = get_crawler(spidercls=EPASpider)
        return EPASpider.from_crawler(crawler)

    def test_default_params(self):
        """Test spider initialization with default parameters."""
        spider = EPASpider()
        assert spider.name == "epa"
        assert len(spider.start_urls) == 1
        assert "cfpub.epa.gov" in spider.start_urls[0]
        assert "count=25" in spider.start_urls[0]
        assert "startIndex" not in spider.start_urls[0]

    def test_custom_params(self):
        """Test spider initialization with custom parameters."""
        terms = "education,research"
        page = "3"
        spider = EPASpider(terms=terms, page=page)

        assert spider.target_page == 3
        assert len(spider.start_urls) == 2
        assert "keyword=education" in spider.start_urls[0]
        assert "keyword=research" in spider.start_urls[1]
        assert "count=25" in spider.start_urls[0]
        assert "startIndex=51" in spider.start_urls[0]

    def test_extract_file_urls(self):
        """Test the extract_file_urls method."""
        html = """
        <div>
            <a href="si_public_file_download.cfm?p_download_id=1">File 1</a>
            <a href="si_public_file_download.cfm?p_download_id=2">File 2</a>
            <a href="other.html">Other link</a>
        </div>
        """
        response = HtmlResponse(url="https://cfpub.epa.gov/si/", body=html.encode("utf-8"))

        urls = EPASpider.extract_file_urls(response)

        assert len(urls) == 2
        assert urls[0] == "https://cfpub.epa.gov/si/si_public_file_download.cfm?p_download_id=1"
        assert urls[1] == "https://cfpub.epa.gov/si/si_public_file_download.cfm?p_download_id=2"

    def test_extract_authors(self):
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
