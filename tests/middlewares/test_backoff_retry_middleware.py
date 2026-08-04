import asyncio
from collections.abc import Callable, Coroutine, Generator
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
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


def _response(request: Request, status: int, headers: dict[str, str] | None = None) -> Response:
    return Response(request.url, status=status, headers=headers, request=request)


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
        response = _response(request_, 200)

        result = run(middleware.process_response(request_, response))

        assert result is response

    def test_passes_through_non_retryable_error_status(
        self, middleware: BackoffRetryMiddleware, request_: Request, run: RunCoroutine
    ) -> None:
        """403/404 are permanent failures and must not be retried."""
        for status in (403, 404):
            response = _response(request_, status)

            result = run(middleware.process_response(request_, response))

            assert result is response

    def test_retries_rate_limited_response(
        self, middleware: BackoffRetryMiddleware, request_: Request, run: RunCoroutine
    ) -> None:
        response = _response(request_, 503)

        result = run(middleware.process_response(request_, response))

        assert isinstance(result, Request)
        assert result.meta["retry_times"] == 1
        assert result.dont_filter is True

    def test_gives_up_on_rate_limit_after_max_retries(
        self, middleware: BackoffRetryMiddleware, request_: Request, run: RunCoroutine
    ) -> None:
        exhausted = request_.replace(meta={"retry_times": middleware.max_retries})
        response = _response(exhausted, 503)

        result = run(middleware.process_response(exhausted, response))

        assert result is response
        # RetryMiddleware must not pick up the request we gave up on.
        assert exhausted.meta["dont_retry"] is True

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
        assert exhausted.meta["dont_retry"] is True

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

    def test_delay_never_exceeds_max_delay(self) -> None:
        middleware = BackoffRetryMiddleware(max_retries=5, base_delay=4.0, max_delay=10.0)

        assert middleware._delay(10) <= 10.0

    def test_retry_after_seconds_header_is_honored(self, request_: Request) -> None:
        middleware = BackoffRetryMiddleware(max_retries=3, base_delay=1.0, max_delay=60.0)
        response = _response(request_, 503, headers={"Retry-After": "2"})

        assert middleware._retry_after(response) == 2.0

    def test_retry_after_is_capped_at_max_delay(
        self, middleware: BackoffRetryMiddleware, request_: Request
    ) -> None:
        response = _response(request_, 503, headers={"Retry-After": "9999"})

        assert middleware._retry_after(response) == middleware.max_delay

    def test_retry_after_http_date_header_is_honored(
        self, middleware: BackoffRetryMiddleware, request_: Request
    ) -> None:
        when = format_datetime(datetime.now(UTC) + timedelta(seconds=0.0015))
        response = _response(request_, 503, headers={"Retry-After": when})

        delay = middleware._retry_after(response)
        assert delay is not None
        assert 0.0 <= delay <= middleware.max_delay

    def test_retry_after_absent_returns_none(
        self, middleware: BackoffRetryMiddleware, request_: Request
    ) -> None:
        assert middleware._retry_after(_response(request_, 503)) is None
