import csv
from collections.abc import Generator
from datetime import date
from pathlib import Path

import pytest
from scrapy.exceptions import IgnoreRequest
from scrapy.http import HtmlResponse, Request
from sqlmodel import Session, SQLModel, create_engine
from twisted.internet.error import DNSLookupError
from twisted.python.failure import Failure

from open_ire.models import Article
from open_ire.spiders.unavailable_articles import (
    UnavailableArticlesSpider,
    _CollectedArticleRecord,
)


@pytest.fixture
def spider(tmp_path: Path) -> Generator[UnavailableArticlesSpider, None, None]:
    spider_instance = UnavailableArticlesSpider(
        output_csv=str(tmp_path / "unavailable_articles.csv"),
    )
    spider_instance.db_batch_size = 2

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    spider_instance.engine = engine

    yield spider_instance

    spider_instance.close(spider_instance, "test_teardown")
    engine.dispose()


def _insert_article(
    spider: UnavailableArticlesSpider,
    reference: str,
    url: str,
    repository: str = "repo_a",
) -> None:
    assert spider.engine is not None

    with Session(spider.engine) as session:
        session.add(
            Article(
                title=f"Article {reference}",
                authors="Author One",
                publication_date=date(2026, 1, 1),
                repository=repository,
                reference=reference,
                url=url,
            )
        )
        session.commit()


class TestUnavailableArticlesSpider:
    @pytest.mark.asyncio
    async def test_start_yields_requests_from_database(
        self, spider: UnavailableArticlesSpider
    ) -> None:
        _insert_article(spider, reference="A1", url="https://example.org/a1")
        _insert_article(spider, reference="A2", url="https://example.org/a2")

        requests = []
        async for request in spider.start():
            requests.append(request)

        assert len(requests) == 2
        assert all(isinstance(request, Request) for request in requests)
        assert all(request.method == "HEAD" for request in requests)
        assert all(request.meta["handle_httpstatus_all"] for request in requests)
        assert all(request.dont_filter for request in requests)

    def test_parse_article_availability(
        self,
        spider: UnavailableArticlesSpider,
    ) -> None:
        article = _CollectedArticleRecord(
            article_id="id-1",
            repository="repo_a",
            reference="A1",
            kind="article_metadata",
            url="https://example.org/a1",
        )
        request = Request(url=article.url)
        response = HtmlResponse(url=article.url, status=404, body=b"", request=request)

        result = spider.parse_article_availability(response, article, "GET")

        assert result is None
        assert spider.repository_stats["repo_a"].checked == 1
        assert spider.repository_stats["repo_a"].unavailable == 1
        assert spider.repository_stats["repo_a"].http_errors == 1

    def test_parse_article_availability_marks_available(
        self,
        spider: UnavailableArticlesSpider,
    ) -> None:
        article = _CollectedArticleRecord(
            article_id="id-success",
            repository="repo_success",
            reference="S1",
            kind="article_metadata",
            url="https://example.org/s1",
        )
        request = Request(url=article.url)
        response = HtmlResponse(url=article.url, status=200, body=b"", request=request)

        result = spider.parse_article_availability(response, article, "HEAD")

        assert result is None
        assert spider.repository_stats["repo_success"].checked == 1
        assert spider.repository_stats["repo_success"].available == 1
        assert spider.repository_stats["repo_success"].unavailable == 0
        assert spider.repository_stats["repo_success"].http_errors == 0
        assert spider.repository_stats["repo_success"].request_errors == 0

    def test_parse_article_availability_fallback(self, spider: UnavailableArticlesSpider) -> None:
        article = _CollectedArticleRecord(
            article_id="id-2",
            repository="repo_b",
            reference="B1",
            kind="article_metadata",
            url="https://example.org/b1",
        )
        request = Request(url=article.url)
        response = HtmlResponse(url=article.url, status=404, body=b"", request=request)

        fallback_request = spider.parse_article_availability(response, article, "HEAD")

        assert isinstance(fallback_request, Request)
        assert fallback_request.method == "GET"
        assert spider.repository_stats["repo_b"].checked == 0

    def test_handle_request_error(self, spider: UnavailableArticlesSpider) -> None:
        article = _CollectedArticleRecord(
            article_id="id-3",
            repository="repo_c",
            reference="C1",
            kind="article_metadata",
            url="https://example.org/c1",
        )
        request = spider._build_request(article, method="GET")
        failure = Failure(DNSLookupError("dns failure"))  # type: ignore[no-untyped-call]
        failure.request = request  # type: ignore[attr-defined]

        spider.handle_request_error(failure)

        assert spider.repository_stats["repo_c"].checked == 1
        assert spider.repository_stats["repo_c"].unavailable == 1
        assert spider.repository_stats["repo_c"].request_errors == 1

    def test_handle_ignore_request_does_not_mark_unavailable(
        self, spider: UnavailableArticlesSpider
    ) -> None:
        article = _CollectedArticleRecord(
            article_id="id-ignore",
            repository="repo_ignore",
            reference="I1",
            kind="article_metadata",
            url="https://example.org/i1",
        )
        request = spider._build_request(article, method="HEAD")
        failure = Failure(IgnoreRequest("filtered"))  # type: ignore[no-untyped-call]
        failure.request = request  # type: ignore[attr-defined]

        spider.handle_request_error(failure)

        assert spider.repository_stats["repo_ignore"].checked == 0
        assert spider.repository_stats["repo_ignore"].unavailable == 0
        assert spider.repository_stats["repo_ignore"].request_errors == 0

    def test_csv_writes(self, spider: UnavailableArticlesSpider) -> None:
        article_a = _CollectedArticleRecord(
            article_id="id-4",
            repository="repo_d",
            reference="D1",
            kind="article_metadata",
            url="https://example.org/d1",
        )
        article_b = _CollectedArticleRecord(
            article_id="id-5",
            repository="repo_d",
            reference="D2",
            kind="article_metadata",
            url="https://example.org/d2",
        )

        request_a = Request(url=article_a.url)
        request_b = Request(url=article_b.url)

        response_a = HtmlResponse(url=article_a.url, status=404, body=b"", request=request_a)
        response_b = HtmlResponse(url=article_b.url, status=500, body=b"", request=request_b)

        spider.parse_article_availability(response_a, article_a, "GET")
        spider.parse_article_availability(response_b, article_b, "HEAD")

        spider.close(spider, "finished")

        csv_path = spider.output_csv
        assert csv_path.exists()

        with csv_path.open("r", encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))

        assert len(rows) == 2
        assert {int(row["status_code"]) for row in rows} == {404, 500}
        assert {row["repository"] for row in rows} == {"repo_d"}
