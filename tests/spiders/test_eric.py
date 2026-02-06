from scrapy.http import HtmlResponse

from open_ire.settings import OPEN_IRE_SEARCH_TERMS
from open_ire.spiders.eric import EricSpider


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
