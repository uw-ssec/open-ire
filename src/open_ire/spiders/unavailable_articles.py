from collections import defaultdict
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO, cast

from scrapy import Spider
from scrapy.exporters import CsvItemExporter
from scrapy.http import Request, Response
from sqlalchemy import Engine
from sqlmodel import asc, create_engine, select
from twisted.internet.defer import Deferred
from twisted.python.failure import Failure

from open_ire.items import UnavailableArticleItem
from open_ire.models import Article


@dataclass(frozen=True, slots=True)
class _CollectedArticleRecord:
    article_id: str
    repository: str
    reference: str
    url: str


@dataclass(slots=True)
class _RepositoryAvailabilityStats:
    checked: int = 0
    available: int = 0
    unavailable: int = 0
    http_errors: int = 0
    request_errors: int = 0

    def add(self, other: "_RepositoryAvailabilityStats") -> None:
        self.checked += other.checked
        self.available += other.available
        self.unavailable += other.unavailable
        self.http_errors += other.http_errors
        self.request_errors += other.request_errors

    def mark_available(self) -> None:
        self.checked += 1
        self.available += 1

    def mark_unavailable(self, *, is_http_error: bool, is_request_error: bool) -> None:
        self.checked += 1
        self.unavailable += 1

        if is_http_error:
            self.http_errors += 1

        if is_request_error:
            self.request_errors += 1


class UnavailableArticleExporter:
    def __init__(self, output_csv: Path) -> None:
        self.output_csv = output_csv
        self._csv_file: BinaryIO | None = None
        self._exporter: CsvItemExporter | None = None

    def open(self) -> None:
        if self._exporter:
            return

        self.output_csv.parent.mkdir(parents=True, exist_ok=True)
        if self.output_csv.exists():
            self.output_csv.unlink()

        self._csv_file = self.output_csv.open("wb")
        self._exporter = CsvItemExporter(
            cast(Any, self._csv_file),
            include_headers_line=True,
            fields_to_export=list(UnavailableArticleItem.model_fields.keys()),
            encoding="utf-8",
        )
        self._exporter.start_exporting()

    def close(self) -> None:
        if self._exporter:
            self._exporter.finish_exporting()
            self._exporter = None

        if self._csv_file:
            self._csv_file.close()
            self._csv_file = None

    def export(self, item: UnavailableArticleItem) -> None:
        if not self._exporter:
            self.open()

        if self._exporter:
            self._exporter.export_item(item)


class UnavailableArticlesSpider(Spider):
    """
    Track previously collected articles that are no longer available at source URL.
    """

    name = "unavailable_articles"

    custom_settings = {  # noqa: RUF012
        "CONCURRENT_REQUESTS": 8,
        "DOWNLOAD_HANDLERS": {
            "http": "scrapy.core.downloader.handlers.http.HTTPDownloadHandler",
            "https": "scrapy.core.downloader.handlers.http.HTTPDownloadHandler",
        },
        "ITEM_PIPELINES": {},
    }

    def __init__(
        self,
        output_csv: str = "output/unavailable_articles.csv",
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)

        self.engine: Engine | None = None
        self.output_csv = Path(output_csv)
        self.db_batch_size = 5000
        self.exporter = UnavailableArticleExporter(self.output_csv)

        self._scheduled_articles = 0
        self._unavailable_items = 0

        self.repository_stats: dict[str, _RepositoryAvailabilityStats] = defaultdict(
            _RepositoryAvailabilityStats
        )

    @staticmethod
    def close(spider: Spider, reason: str) -> Deferred[None] | None:
        close_result = Spider.close(spider, reason)

        if isinstance(spider, UnavailableArticlesSpider):
            spider.exporter.close()
            spider._log_summary(reason)

            if spider.engine:
                spider.engine.dispose()

        return close_result

    @classmethod
    def from_crawler(cls, crawler: Any, *args: Any, **kwargs: Any) -> "UnavailableArticlesSpider":
        spider = super().from_crawler(crawler, *args, **kwargs)

        if db_path := crawler.settings.get("OPEN_IRE_DATABASE_FILE"):
            spider.engine = create_engine(
                f"sqlite:///{db_path}",
                connect_args={"check_same_thread": False},
            )

        return spider

    def _iter_articles(self) -> Iterator[_CollectedArticleRecord]:
        if not self.engine:
            return

        statement = (
            select(
                Article.id,
                Article.repository,
                Article.reference,
                Article.url,
            )
            .where(Article.url != "")
            .order_by(asc(Article.created_at))
        )

        with self.engine.connect() as connection:
            result = connection.execution_options(stream_results=True).execute(statement)

            while rows := result.fetchmany(self.db_batch_size):
                for row in rows:
                    article_id, repository, reference, url = row

                    if not isinstance(url, str):
                        continue

                    normalized_url = url.strip()
                    if not normalized_url:
                        continue

                    yield _CollectedArticleRecord(
                        article_id=str(article_id),
                        repository=str(repository),
                        reference=str(reference),
                        url=normalized_url,
                    )

    def _build_request(self, article: _CollectedArticleRecord, method: str) -> Request:
        return Request(
            article.url,
            method=method,
            callback=self.parse_article_availability,
            errback=self.handle_request_error,
            cb_kwargs={
                "article": article,
                "request_method": method,
            },
            meta={"handle_httpstatus_all": True},
            dont_filter=True,
        )

    def _record_unavailable(
        self,
        article: _CollectedArticleRecord,
        status_code: int | None,
        error: str,
        request_method: str,
        is_http_error: bool = False,
        is_request_error: bool = False,
    ) -> None:
        self._update_stats(
            article.repository,
            available=False,
            is_http_error=is_http_error,
            is_request_error=is_request_error,
        )

        unavailable_item = UnavailableArticleItem(
            article_id=article.article_id,
            repository=article.repository,
            reference=article.reference,
            url=article.url,
            status_code=status_code,
            error=error,
            request_method=request_method,
            checked_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        self._export_unavailable_item(unavailable_item)

    def _export_unavailable_item(self, item: UnavailableArticleItem) -> None:
        self.exporter.export(item)
        self._unavailable_items += 1

    def _update_stats(
        self,
        repository: str,
        available: bool,
        is_http_error: bool = False,
        is_request_error: bool = False,
    ) -> None:
        repository_stats = self.repository_stats[repository]

        if available:
            repository_stats.mark_available()
            return

        repository_stats.mark_unavailable(
            is_http_error=is_http_error,
            is_request_error=is_request_error,
        )

    def _log_summary(self, reason: str) -> None:
        totals = _RepositoryAvailabilityStats()

        for repository_stats in self.repository_stats.values():
            totals.add(repository_stats)

        self.logger.info(
            "Unavailable-article check completed (reason=%s). "
            "Articles checked=%d, available=%d, unavailable=%d",
            reason,
            totals.checked,
            totals.available,
            totals.unavailable,
        )
        self.logger.info(
            "Unavailable article CSV: %s (%d rows)",
            self.output_csv,
            self._unavailable_items,
        )

        if not self.repository_stats:
            self.logger.info("No repository statistics collected.")
            return

        for repository in sorted(self.repository_stats):
            repository_stats = self.repository_stats[repository]
            self.logger.info(
                "Repository=%s checked=%d available=%d unavailable=%d "
                "http_errors=%d request_errors=%d",
                repository,
                repository_stats.checked,
                repository_stats.available,
                repository_stats.unavailable,
                repository_stats.http_errors,
                repository_stats.request_errors,
            )

    async def start(self) -> AsyncIterator[Request]:
        if not self.engine:
            self.logger.error(
                "No database engine available. Ensure OPEN_IRE_DATABASE_FILE is configured."
            )
            return

        self.exporter.open()

        for article in self._iter_articles():
            self._scheduled_articles += 1

            try:
                yield self._build_request(article, method="HEAD")
            except ValueError as exc:
                self._record_unavailable(
                    article,
                    status_code=None,
                    error=f"InvalidURL: {exc}",
                    request_method="HEAD",
                    is_request_error=True,
                )

        self.logger.info(
            "Scheduled availability checks for %d articles",
            self._scheduled_articles,
        )

    def parse_article_availability(
        self,
        response: Response,
        article: _CollectedArticleRecord,
        request_method: str,
    ) -> Request | None:
        if request_method == "HEAD" and response.status in {405, 501}:
            return self._build_request(article, method="GET")

        if response.status >= 400:
            self._record_unavailable(
                article,
                status_code=response.status,
                error=f"HTTP {response.status}",
                request_method=request_method,
                is_http_error=True,
            )
            return None

        self._update_stats(article.repository, available=True)
        return None

    def handle_request_error(self, failure: Failure) -> None:
        request = getattr(failure, "request", None)
        if not isinstance(request, Request):
            self.logger.warning("Request failed without request metadata: %s", failure.value)
            return

        article = request.cb_kwargs.get("article")
        request_method = str(request.cb_kwargs.get("request_method") or request.method)

        if not isinstance(article, _CollectedArticleRecord):
            self.logger.warning("Request failed without article metadata: %s", failure.value)
            return

        response = getattr(failure.value, "response", None)
        status_code = response.status if response is not None else None
        error_name = type(failure.value).__name__

        self._record_unavailable(
            article,
            status_code=status_code,
            error=error_name,
            request_method=request_method,
            is_http_error=status_code is not None,
            is_request_error=True,
        )
