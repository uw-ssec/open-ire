import asyncio
from collections.abc import Callable, Coroutine, Generator
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from scrapy import Request, Spider
from scrapy.crawler import Crawler
from scrapy.http import Response
from scrapy.settings import Settings
from scrapy.statscollectors import MemoryStatsCollector
from twisted.internet.error import ConnectionLost, DNSLookupError

from open_ire.middlewares import BackoffRetryMiddleware

RunCoroutine = Callable[[Coroutine[Any, Any, Any]], Any]


class TestBackoffRetryMiddleware:
    """Tests the BackoffRetryMiddleware backoff, give-up, and pass-through behavior."""

    @pytest.fixture
    def run(self) -> Generator[RunCoroutine, None, None]:
        """Run a coroutine on a private event loop, without touching global loop state."""
        loop = asyncio.new_event_loop()
        yield loop.run_until_complete
        loop.close()

    @pytest.fixture
    def crawler(self) -> Crawler:
        mock_crawler = MagicMock(spec=Crawler)
        mock_crawler.settings = Settings(
            {
                "OPEN_IRE_BACKOFF_RETRY_TIMES": 3,
                "OPEN_IRE_BACKOFF_BASE_DELAY": 0.001,
                "OPEN_IRE_BACKOFF_MAX_DELAY": 0.002,
            }
        )
        mock_crawler.stats = MemoryStatsCollector(mock_crawler)

        spider = Spider(name="test_spider")
        spider.crawler = mock_crawler
        mock_crawler.spider = spider

        return cast(Crawler, mock_crawler)

    @pytest.fixture
    def middleware(self, crawler: Crawler) -> BackoffRetryMiddleware:
        return BackoffRetryMiddleware.from_crawler(crawler)

    @pytest.fixture
    def request_(self) -> Request:
        return Request("https://www.osti.gov/servlets/purl/1016225")

    def test_from_crawler_reads_settings(self, middleware: BackoffRetryMiddleware) -> None:
        assert middleware.max_retries == 3
        assert middleware.base_delay == 0.001
        assert middleware.max_delay == 0.002

    def test_passes_through_ok_response(
        self, middleware: BackoffRetryMiddleware, request_: Request, run: RunCoroutine
    ) -> None:
        response = Response(request_.url, status=200, request=request_)

        result = run(middleware.process_response(request_, response))

        assert result is response

    def test_passes_through_non_retryable_error_status(
        self, middleware: BackoffRetryMiddleware, request_: Request, run: RunCoroutine
    ) -> None:
        """403/404 are permanent failures and must not be retried."""
        for status in (403, 404):
            response = Response(request_.url, status=status, request=request_)

            result = run(middleware.process_response(request_, response))

            assert result is response

    def test_retries_rate_limited_response(
        self, middleware: BackoffRetryMiddleware, request_: Request, run: RunCoroutine
    ) -> None:
        response = Response(request_.url, status=503, request=request_)

        result = run(middleware.process_response(request_, response))

        assert isinstance(result, Request)
        assert result.meta["retry_times"] == 1
        assert result.dont_filter is True

    def test_gives_up_on_rate_limit_after_max_retries(
        self, middleware: BackoffRetryMiddleware, request_: Request, run: RunCoroutine
    ) -> None:
        exhausted = request_.replace(meta={"retry_times": middleware.max_retries})
        response = Response(exhausted.url, status=503, request=exhausted)

        result = run(middleware.process_response(exhausted, response))

        assert result is response

    def test_retries_dropped_connection(
        self, middleware: BackoffRetryMiddleware, request_: Request, run: RunCoroutine
    ) -> None:
        result = run(middleware.process_exception(request_, ConnectionLost("dropped")))

        assert isinstance(result, Request)
        assert result.meta["retry_times"] == 1

    def test_gives_up_on_dropped_connection_after_max_retries(
        self, middleware: BackoffRetryMiddleware, request_: Request, run: RunCoroutine
    ) -> None:
        exhausted = request_.replace(meta={"retry_times": middleware.max_retries})

        result = run(middleware.process_exception(exhausted, ConnectionLost("dropped")))

        assert result is None

    def test_ignores_dns_failure(
        self, middleware: BackoffRetryMiddleware, request_: Request, run: RunCoroutine
    ) -> None:
        """Dead hosts (DNS gone) are left to the built-in RetryMiddleware."""
        result = run(middleware.process_exception(request_, DNSLookupError("no such host")))

        assert result is None

    def test_delay_backs_off_exponentially_within_jitter(self) -> None:
        middleware = BackoffRetryMiddleware(max_retries=5, base_delay=4.0, max_delay=1000.0)

        for retry_times, base in ((0, 4.0), (1, 8.0), (3, 32.0)):
            delay = middleware._delay(retry_times)
            assert base * 0.75 <= delay <= base * 1.25

    def test_delay_is_capped(self) -> None:
        middleware = BackoffRetryMiddleware(max_retries=5, base_delay=4.0, max_delay=10.0)

        assert middleware._delay(10) <= 10.0 * 1.25
