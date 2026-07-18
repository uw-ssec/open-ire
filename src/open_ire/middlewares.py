"""Downloader middlewares for the open_ire project."""

import asyncio
import logging
import random
from typing import Self

from scrapy import Request
from scrapy.crawler import Crawler
from scrapy.downloadermiddlewares.retry import get_retry_request
from scrapy.http import Response
from twisted.internet.error import ConnectError, ConnectionDone, ConnectionLost
from twisted.internet.error import TimeoutError as TxTimeoutError
from twisted.web.client import ResponseFailed

logger = logging.getLogger(__name__)


class BackoffRetryMiddleware:
    """Retry rate-limited or dropped downloads with exponential backoff.

    Some servers (e.g. OSTI's ``/servlets/purl/`` fulltext endpoint) answer
    request bursts with HTTP 503 or drop the connection instead of failing
    permanently. Scrapy's built-in ``RetryMiddleware`` retries immediately,
    which keeps hitting the same rate limit. This middleware instead waits
    with exponential backoff (plus jitter) before each retry, and gives up
    after ``OPEN_IRE_BACKOFF_RETRY_TIMES`` attempts so genuinely dead links
    are skipped (and logged) rather than retried forever.

    DNS lookup failures are deliberately not handled here: a host that no
    longer resolves is dead, and the built-in ``RetryMiddleware`` already
    gives those a couple of quick retries before the file is skipped.

    Enable per spider via ``custom_settings``::

        "DOWNLOADER_MIDDLEWARES": {
            "open_ire.middlewares.BackoffRetryMiddleware": 560,
        }

    Settings (all optional):

    - ``OPEN_IRE_BACKOFF_RETRY_TIMES``: max retries per request (default 5)
    - ``OPEN_IRE_BACKOFF_BASE_DELAY``: first delay in seconds (default 5)
    - ``OPEN_IRE_BACKOFF_MAX_DELAY``: delay cap in seconds (default 120)
    """

    RETRY_STATUSES = frozenset({429, 503})
    RETRY_EXCEPTIONS = (
        ConnectError,
        ConnectionDone,
        ConnectionLost,
        ResponseFailed,
        TxTimeoutError,
    )

    def __init__(
        self,
        max_retries: int,
        base_delay: float,
        max_delay: float,
        crawler: Crawler | None = None,
    ) -> None:
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.crawler = crawler

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> Self:
        settings = crawler.settings
        return cls(
            max_retries=settings.getint("OPEN_IRE_BACKOFF_RETRY_TIMES", 5),
            base_delay=settings.getfloat("OPEN_IRE_BACKOFF_BASE_DELAY", 5.0),
            max_delay=settings.getfloat("OPEN_IRE_BACKOFF_MAX_DELAY", 120.0),
            crawler=crawler,
        )

    def _delay(self, retry_times: int) -> float:
        delay = min(self.base_delay * (2.0**retry_times), self.max_delay)
        return delay * random.uniform(0.75, 1.25)

    async def _retry(self, request: Request, reason: str) -> Request | None:
        if self.crawler is None or self.crawler.spider is None:
            msg = "BackoffRetryMiddleware requires a crawler with a running spider."
            raise RuntimeError(msg)

        retry_times: int = request.meta.get("retry_times", 0)
        new_request = get_retry_request(
            request,
            spider=self.crawler.spider,
            reason=reason,
            max_retry_times=self.max_retries,
        )
        if new_request is None:
            logger.warning(
                "Giving up on %s after %d backoff retries (%s); skipping.",
                request.url,
                retry_times,
                reason,
            )
            return None

        delay = self._delay(retry_times)
        logger.info(
            "Backing off %.1fs before retry %d/%d for %s (%s)",
            delay,
            retry_times + 1,
            self.max_retries,
            request.url,
            reason,
        )
        await asyncio.sleep(delay)
        return new_request

    async def process_response(self, request: Request, response: Response) -> Request | Response:
        if response.status not in self.RETRY_STATUSES:
            return response

        retried = await self._retry(request, f"HTTP {response.status}")
        return response if retried is None else retried

    async def process_exception(self, request: Request, exception: Exception) -> Request | None:
        if not isinstance(exception, self.RETRY_EXCEPTIONS):
            return None

        return await self._retry(request, type(exception).__name__)
