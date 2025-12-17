from datetime import date

from scrapy.http import HtmlResponse
from scrapy.http import Request

from open_ire.items import ArticleItem
from open_ire.settings import OPEN_IRE_SEARCH_TERMS
from open_ire.spiders.epa import EPASpider


class TestEPASpider:
    def test_default_params(self):
        """Test spider initialization with default parameters."""
        spider = EPASpider()
        assert spider.name == "epa"
        assert len(spider.start_urls) == len(OPEN_IRE_SEARCH_TERMS)
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
        assert "searchall=education" in spider.start_urls[0]
        assert "searchall=research" in spider.start_urls[1]
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

        spider = EPASpider(terms="test", page="1")
        urls = spider.extract_file_urls(response)

        assert len(urls) == 2
        assert "https://cfpub.epa.gov/si/si_public_file_download.cfm?p_download_id=1" in urls
        assert "https://cfpub.epa.gov/si/si_public_file_download.cfm?p_download_id=2" in urls

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

    def test_parse_datagov_detail(self):
        """Test parsing dataset file reference URLs from data.gov."""

        item = ArticleItem(
            publication_date=date(2025, 8, 15),
            reference="REF123",
            repository="test",
            title="Test Article",
            url="https://example.com",
        )
        request = Request(
            url="https://catalog.data.gov/dataset/sample-dataset",
            meta={
                "item": item,
                "dataset_urls": [],
                "file_reference_urls": []
            }
        )

        html = """
        <div>
            <ul class="resource-list">
                <li><a class="btn btn-primary" href="/download/file1.csv">Download CSV</a></li>
                <li><a class="btn btn-primary" href="/download/file2.json">Download JSON</a></li>
            </ul>
        </div>
        """
        response = HtmlResponse(
            url=request.url,
            body=html.encode("utf-8"),
            request=request
        )

        spider = EPASpider()
        results = list(spider.parse_datagov_detail(response))

        assert len(results) == 1
        result_item = results[0]
        assert isinstance(result_item, ArticleItem)
        assert len(result_item.file_reference_urls) == 2
        assert result_item.file_reference_urls[0] == (
            "https://catalog.data.gov/dataset/sample-dataset",
            "https://catalog.data.gov/download/file1.csv"
        )
        assert result_item.file_reference_urls[1] == (
            "https://catalog.data.gov/dataset/sample-dataset",
            "https://catalog.data.gov/download/file2.json"
        )
