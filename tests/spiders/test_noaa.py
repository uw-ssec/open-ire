from scrapy.http import HtmlResponse

from open_ire.settings import OPEN_IRE_SEARCH_TERMS
from open_ire.spiders.noaa import NOAASpider


class TestNOAASpider:
    def test_default_params(self) -> None:
        """Test spider initialization with default parameters."""
        spider = NOAASpider()
        assert spider.name == "noaa"
        assert len(spider.start_urls) == len(OPEN_IRE_SEARCH_TERMS)
        assert "repository.library.noaa.gov" in spider.start_urls[0]
        assert "maxResults=100" in spider.start_urls[0]
        assert "start=" not in spider.start_urls[0]

    def test_custom_params(self) -> None:
        """Test spider initialization with custom parameters."""
        terms = "climate,ocean"
        page = "2"
        spider = NOAASpider(terms=terms, page=page)

        assert spider.target_page == 2
        assert len(spider.start_urls) == 2
        assert "terms=climate" in spider.start_urls[0]
        assert "terms=ocean" in spider.start_urls[1]
        assert "maxResults=100" in spider.start_urls[0]
        assert "start=100" in spider.start_urls[0]

    def test_parse(self) -> None:
        """Test the parse method extracts article links and next page."""
        html = """
        <html>
            <body>
                <div class="search-result-row">
                    <div class="object-title">
                        <a href="/view/noaa/123">Article 1</a>
                    </div>
                </div>
                <div class="search-result-row">
                    <div class="object-title">
                        <a href="/view/noaa/456">Article 2</a>
                    </div>
                </div>
                <a id="nextPage" href="/gsearch?start=100">Next</a>
            </body>
        </html>
        """
        response = HtmlResponse(
            url="https://repository.library.noaa.gov/gsearch", body=html.encode("utf-8")
        )
        spider = NOAASpider()

        requests = list(spider.parse(response))

        assert len(requests) == 3  # 2 articles + 1 next page
        assert requests[0].callback == spider.parse_detail
        assert requests[0].url == "https://repository.library.noaa.gov/view/noaa/123"
        assert requests[1].url == "https://repository.library.noaa.gov/view/noaa/456"
        assert requests[2].url == "https://repository.library.noaa.gov/gsearch?start=100"

    def test_parse_detail(self) -> None:
        """Test the parse_detail method extracts article metadata correctly."""
        html = """
        <html>
            <head>
                <meta name="citation_title" content="Unit Test Title">
                <meta name="citation_abstract" content="Unit Test Abstract">
                <meta name="citation_doi" content="10.1000/1234">
                <meta name="citation_author" content="M. Rebecca O'Connor">
                <meta name="citation_author" content="Kaboni Whitney Gondwe">
                <meta name="citation_publication_date" content="2025-07-25">
                <meta name="citation_issn" content="1234-5678">
                <meta name="citation_pdf_url" content="/documents/test.pdf">
                <meta name="citation_volume" content="Volume !">
            </head>
            <body></body>
        </html>
        """
        response = HtmlResponse(
            url="https://repository.library.noaa.gov/view/noaa/1234/",
            body=html.encode("utf-8"),
        )
        spider = NOAASpider()

        items = list(spider.parse_detail(response))

        assert len(items) == 1
        item = items[0]
        assert item.title == "Unit Test Title"
        assert item.abstract == "Unit Test Abstract"
        assert item.doi == "10.1000/1234"
        assert item.authors == "O'Connor, M. Rebecca; Gondwe, Kaboni Whitney"
        assert str(item.publication_date) == "2025-07-25"
        assert item.issn == "1234-5678"
        assert item.reference == "1234"
        assert item.repository == "noaa"
        assert item.url == "https://repository.library.noaa.gov/view/noaa/1234/"
        assert len(item.file_urls) == 1
        assert hasattr(item, "extra")

    def test_extract_file_urls(self) -> None:
        """Test the extract_file_urls method."""
        html = """
        <html>
            <head>
                <meta name="citation_pdf_url" content="/documents/file1.pdf">
                <meta name="citation_pdf_url" content="/documents/file2.pdf">
            </head>
            <body></body>
        </html>
        """
        response = HtmlResponse(
            url="https://repository.library.noaa.gov/", body=html.encode("utf-8")
        )

        urls = NOAASpider._extract_file_urls(response)

        assert len(urls) == 2
        assert urls[0] == "https://repository.library.noaa.gov/documents/file1.pdf"
        assert urls[1] == "https://repository.library.noaa.gov/documents/file2.pdf"

    def test_extract_extra_details(self) -> None:
        """Test the extract_extra_details method."""
        html = """
        <html>
            <head>
                <meta name="citation_volume" content="Volume 1">
                <meta name="citation_publisher" content="NOAA Publications">
                <meta name="citation_journal_title" content="Unit Test Journal">
                <meta name="citation_conference" content="Unit Test Conference">
                <meta name="citation_language" content="English">
                <meta name="citation_keywords" content="climate">
                <meta name="citation_keywords" content="ocean">
                <meta name="citation_keywords" content="climate">
            </head>
            <body>
                <textarea id="Genericpreview">Unit Test Citation</textarea>
            </body>
        </html>
        """
        response = HtmlResponse(
            url="https://repository.library.noaa.gov/", body=html.encode("utf-8")
        )

        spider = NOAASpider()
        extra = spider._extract_extra_details(response)

        assert extra["volume"] == "Volume 1"
        assert extra["publisher"] == "NOAA Publications"
        assert extra["journal_title"] == "Unit Test Journal"
        assert extra["conference"] == "Unit Test Conference"
        assert extra["language"] == "English"
        assert extra["citation_text"] == "Unit Test Citation"
        assert "climate" in extra["keywords"]
        assert "ocean" in extra["keywords"]
        assert len(extra["keywords"]) == 2

    def test_extract_extra_details_empty(self) -> None:
        """Test the extract_extra_details method with an empty response."""
        html = "<html><head></head><body></body></html>"
        response = HtmlResponse(
            url="https://repository.library.noaa.gov/", body=html.encode("utf-8")
        )

        spider = NOAASpider()
        extra = spider._extract_extra_details(response)

        assert extra == {}

    def test_extract_journal_title_from_details_list(self) -> None:
        """Test journal title fallback from details list rows."""
        html = """
        <html>
            <head></head>
            <body>
                <ul class="bookDetailsList">
                    <li class="bookDetails-row">
                        <div class="bookDetailsLabel">
                            <b>Journal Title:</b>
                        </div>
                        <div class="bookDetailsData pt-3">
                            <div>NOAA Details Journal</div>
                        </div>
                    </li>
                </ul>
            </body>
        </html>
        """
        response = HtmlResponse(
            url="https://repository.library.noaa.gov/", body=html.encode("utf-8")
        )
        spider = NOAASpider()

        extra = spider._extract_extra_details(response)

        assert extra["journal_title"] == "NOAA Details Journal"
