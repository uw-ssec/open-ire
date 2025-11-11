from pathlib import Path

import pytest
from scrapy.crawler import Crawler
from scrapy.http import HtmlResponse, Request
from scrapy.spiders import Spider
from scrapy.settings import Settings

from open_ire.errors import ConfigurationError
from open_ire.middlewares import SaveResponsesMiddleware


class TestMiddlewares:
    def test_from_crawler_raises_not_configured_if_setting_is_missing(self):
        crawler = Crawler(Spider, settings=Settings())
        with pytest.raises(ConfigurationError):
            SaveResponsesMiddleware.from_crawler(crawler)

    def test_middleware_saves_response_correctly(self, tmp_path: Path):
        save_dir = tmp_path / "responses"
        middleware = SaveResponsesMiddleware(save_dir=save_dir)

        # Create a dummy spider and response for the test
        spider = Spider(name="test_spider")
        url = "http://example.com/test-page"
        body = b"<html><body>Test content</body></html>"
        request = Request(url)
        response = HtmlResponse(url, request=request, body=body)

        # The middleware should return None and not stop the response
        assert middleware.process_spider_input(response, spider) is None  # type: ignore[func-returns-value]

        # Check that the directory was created
        assert save_dir.exists()
        assert save_dir.is_dir()

        # Check that both request and response files were created
        files = list(save_dir.iterdir())
        assert files and all(f.name.endswith((".txt", ".html", ".json")) for f in files)

        # Check that the file content is correct
        html_file = next((f for f in files if f.name.endswith(".html")), None)
        assert html_file is not None
        assert html_file.read_bytes() == body

        json_file = next((f for f in files if f.name.endswith(".json")), None)
        assert json_file is not None
        import json
        parsed = json.loads(json_file.read_text())
        assert parsed.get("url") == request.url
