from scrapy.http import HtmlResponse

from open_ire.settings import OPEN_IRE_DEFAULT_TERMS
from open_ire.spiders.cdc_stacks import CDCStacksSpider


class TestCDCStacksSpider:
    def test_default_params(self):
        """Test spider initialization with default parameters."""
        spider = CDCStacksSpider()
        assert spider.name == "cdc_stacks"
        assert len(spider.start_urls) == len(OPEN_IRE_DEFAULT_TERMS.split(","))
        assert "stacks.cdc.gov" in spider.start_urls[0]
        assert "gsearch?terms=" in spider.start_urls[0]
        assert "start=" not in spider.start_urls[0]

    def test_custom_params(self):
        """Test spider initialization with custom parameters."""
        terms = "unittest,sample"
        page = "2"
        spider = CDCStacksSpider(terms=terms, page=page)

        assert spider.target_page == 2
        assert len(spider.start_urls) == 2
        assert "terms=unittest" in spider.start_urls[0]
        assert "terms=sample" in spider.start_urls[1]
        assert "start=21" in spider.start_urls[0]

    def test_parse_next(self):
        """Test the parse method yields the next requests."""
        html = """
        <html>
            <body>
                <div class="search-result-row">
                    <div class="object-title">
                        <a href="/view/cdc/123">Article 1</a>
                    </div>
                </div>
                <div class="search-result-row">
                    <div class="object-title">
                        <a href="/view/cdc/456">Article 2</a>
                    </div>
                </div>
                <a id="nextPage" href="/gsearch?start=21">Next</a>
            </body>
        </html>
        """
        response = HtmlResponse(
            url="https://stacks.cdc.gov/gsearch", body=html.encode("utf-8")
        )
        spider = CDCStacksSpider()

        requests = list(spider.parse(response))

        assert len(requests) == 2
        assert requests[0].url == "https://stacks.cdc.gov/view/cdc/123"
        assert requests[0].callback == spider.parse_detail
        assert requests[1].url == "https://stacks.cdc.gov/gsearch?start=21"

    def test_parse_detail(self):
        """Test the parse_detail method extracts article metadata correctly."""
        html = """
        <html>
            <head>
                <meta name="citation_title" content="Unit Test Title">
                <meta name="citation_abstract" content="Unit Test Abstract">
                <meta name="citation_doi" content="10.1000/stackstest">
                <meta name="citation_author" content="Unit Test Author 1">
                <meta name="citation_author" content="Unit Test Author 2">
                <meta name="citation_publication_date" content="2025-09-15">
                <meta name="citation_issn" content="1234-5678">
                <meta name="citation_pdf_url" content="/documents/test.pdf">
                <meta name="citation_volume" content="Volume 1">
                <meta name="citation_publisher" content="CDC Publications">
                <meta name="citation_keywords" content="health">
                <meta name="citation_keywords" content="public health">
                <meta name="citation_keywords" content="health">
            </head>
            <body></body>
        </html>
        """
        response = HtmlResponse(
            url="https://stacks.cdc.gov/view/cdc/1234/", body=html.encode("utf-8")
        )
        spider = CDCStacksSpider()

        items = list(spider.parse_detail(response))

        assert len(items) == 1
        item = items[0]
        assert item.title == "Unit Test Title"
        assert item.abstract == "Unit Test Abstract"
        assert item.doi == "10.1000/stackstest"
        assert item.authors == "Unit Test Author 1, Unit Test Author 2"
        assert str(item.publication_date) == "2025-09-15"
        assert item.issn == "1234-5678"
        assert item.reference == "1234"
        assert item.repository == "cdc_stacks"
        assert item.url == "https://stacks.cdc.gov/view/cdc/1234/"
        assert len(item.file_urls) == 1
        assert item.file_urls[0] == "https://stacks.cdc.gov/documents/test.pdf"
        assert item.extra["volume"] == "Volume 1"
        assert item.extra["publisher"] == "CDC Publications"
        assert set(item.extra["keywords"]) == {"health", "public health"}

    def test_extract_reference(self):
        """Test _extract_reference falls back to full URL when CDC ID is not in path."""
        html = "<html><head></head><body></body></html>"
        url = "https://stacks.cdc.gov/some/other/path/123"
        response = HtmlResponse(url=url, body=html.encode("utf-8"))
        spider = CDCStacksSpider()

        reference = spider._extract_reference(response)
        assert reference == url
