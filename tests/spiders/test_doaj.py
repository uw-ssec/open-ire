import json
import uuid
from collections.abc import Generator
from datetime import date
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from scrapy.http import HtmlResponse
from sqlmodel import Session, select

from open_ire.enums import DepositStatus, DepositTransitionReason, DepositWarrant
from open_ire.models import Article, ArticleDepositStatusTransition, ArticleDepositWarrant
from open_ire.spiders.doaj import DOAJSpider, _ArticleCandidate


@pytest.fixture
def spider() -> Generator[DOAJSpider, None, None]:
    with patch.object(DOAJSpider, "logger", new_callable=MagicMock):
        spider_instance = DOAJSpider()
        yield spider_instance


class TestCandidates:
    def test_query_article_candidates_filters_and_normalizes(
        self, spider_with_db: DOAJSpider
    ) -> None:
        with Session(spider_with_db.engine) as session:
            session.add(
                Article(
                    title="Valid DOI",
                    authors="A",
                    publication_date=date(2023, 1, 1),
                    repository="repo",
                    reference="R1",
                    url="https://example.com/1",
                    doi="https://doi.org/10.5555/valid",
                )
            )
            session.add(
                Article(
                    title="Invalid DOI",
                    authors="B",
                    publication_date=date(2023, 1, 2),
                    repository="repo",
                    reference="R2",
                    url="https://example.com/2",
                    doi="not-a-doi",
                )
            )
            session.add(
                Article(
                    title="No DOI",
                    authors="C",
                    publication_date=date(2023, 1, 3),
                    repository="repo",
                    reference="R3",
                    url="https://example.com/3",
                    doi=None,
                )
            )
            session.commit()

        candidates = spider_with_db.query_article_candidates()

        assert len(candidates) == 1
        assert candidates[0].doi == "10.5555/valid"


class TestRequests:
    def test_build_search_request(self, spider: DOAJSpider, article_id: uuid.UUID) -> None:
        candidate = _ArticleCandidate(
            article_id=article_id,
            doi="10.1234/example",
        )

        request = spider.build_search_request(candidate)

        assert request.url == "https://doaj.org/api/search/articles/doi:10.1234%2Fexample"
        assert request.cb_kwargs["candidate"] == candidate


class TestMatching:
    def test_match_article_by_doi(self, spider: DOAJSpider) -> None:
        results: list[dict[str, Any]] = [
            {
                "id": "article-1",
                "bibjson": {
                    "title": "Test Article",
                    "identifier": [
                        {"type": "doi", "id": "10.1234/test.doi"},
                        {"type": "eissn", "id": "1860-5974"},
                    ],
                },
            }
        ]

        match = spider.match_article_by_doi(results, "10.1234/test.doi")

        assert match is not None
        assert match["supports_oa"] is True
        assert match["allow_transition"] is True
        assert match["confidence"] == "high"


class TestParseFlow:
    def test_parse_search_response_non_match_does_not_save_evidence(
        self, spider: DOAJSpider, article_id: uuid.UUID
    ) -> None:
        candidate = _ArticleCandidate(
            article_id=article_id,
            doi="10.1234/test.doi",
        )

        response_data: dict[str, Any] = {"total": 0, "results": []}
        response = HtmlResponse(
            url="https://doaj.org/api/search/articles/doi:10.1234/test.doi",
            body=json.dumps(response_data).encode(),
            encoding="utf-8",
        )
        spider.save_doaj_warrant = MagicMock()  # type: ignore[method-assign]

        result = spider.parse_search_response(response, candidate)

        assert result is None
        spider.save_doaj_warrant.assert_not_called()

    def test_parse_search_response_saves_match(
        self, spider: DOAJSpider, article_id: uuid.UUID
    ) -> None:
        candidate = _ArticleCandidate(article_id=article_id, doi="10.1234/test.doi")
        response_data: dict[str, Any] = {
            "total": 1,
            "results": [
                {
                    "id": "article-1",
                    "bibjson": {
                        "title": "Test Article",
                        "identifier": [{"type": "doi", "id": "10.1234/test.doi"}],
                    },
                }
            ],
        }
        response = HtmlResponse(
            url="https://doaj.org/api/search/articles/doi:10.1234/test.doi",
            body=json.dumps(response_data).encode(),
            encoding="utf-8",
        )
        spider.save_doaj_warrant = MagicMock()  # type: ignore[method-assign]

        result = spider.parse_search_response(response, candidate)

        assert result is None
        spider.save_doaj_warrant.assert_called_once()

    def test_parse_search_response_http_error_does_not_save(
        self, spider: DOAJSpider, article_id: uuid.UUID
    ) -> None:
        candidate = _ArticleCandidate(article_id=article_id, doi="10.1234/test.doi")
        response = HtmlResponse(
            url="https://doaj.org/api/search/articles/doi:10.1234/test.doi",
            status=503,
            body=b"",
            encoding="utf-8",
        )
        spider.save_doaj_warrant = MagicMock()  # type: ignore[method-assign]

        result = spider.parse_search_response(response, candidate)

        assert result is None
        spider.save_doaj_warrant.assert_not_called()


class TestSaveDOAJWarrant:
    def test_save_supporting_warrant_creates_transition(
        self, spider_with_db: DOAJSpider, sample_article: Article
    ) -> None:
        spider_with_db.save_doaj_warrant(
            sample_article.id,
            supports_oa=True,
            allow_transition=True,
            warrant_data={"matched": True, "match_strategy": "article_doi"},
        )

        with Session(spider_with_db.engine) as session:
            warrant = session.exec(
                select(ArticleDepositWarrant).where(
                    ArticleDepositWarrant.article_id == sample_article.id
                )
            ).first()
            assert warrant is not None
            assert warrant.kind == DepositWarrant.EXTERNAL_OA
            assert warrant.source == "doaj"
            assert warrant.supports_oa is True

            transition = session.exec(
                select(ArticleDepositStatusTransition).where(
                    ArticleDepositStatusTransition.article_id == sample_article.id
                )
            ).first()
            assert transition is not None
            assert transition.to_status == DepositStatus.READY
            assert DepositTransitionReason.EXTERNAL_OA in transition.reasons

    def test_save_non_supporting_warrant_without_transition(
        self, spider_with_db: DOAJSpider, sample_article: Article
    ) -> None:
        spider_with_db.save_doaj_warrant(
            sample_article.id,
            supports_oa=False,
            allow_transition=False,
            warrant_data={"matched": False},
        )

        with Session(spider_with_db.engine) as session:
            warrant = session.exec(
                select(ArticleDepositWarrant).where(
                    ArticleDepositWarrant.article_id == sample_article.id
                )
            ).first()
            assert warrant is not None
            assert warrant.supports_oa is False

            transition = session.exec(
                select(ArticleDepositStatusTransition).where(
                    ArticleDepositStatusTransition.article_id == sample_article.id
                )
            ).first()
            assert transition is None


class TestStart:
    @pytest.mark.asyncio
    async def test_start_skips_article_with_existing_warrant(
        self, spider_with_db: DOAJSpider, sample_article: Article
    ) -> None:
        with Session(spider_with_db.engine) as session:
            session.add(
                ArticleDepositWarrant(
                    article_id=sample_article.id,
                    kind=DepositWarrant.LICENSE,
                    supports_oa=True,
                    source="crossref",
                    data={},
                )
            )
            session.commit()

        requests = [request async for request in spider_with_db.start()]

        assert requests == []

    @pytest.mark.asyncio
    async def test_start_emits_request_when_no_existing_warrant(
        self, spider_with_db: DOAJSpider, sample_article: Article
    ) -> None:
        requests = [request async for request in spider_with_db.start()]

        assert len(requests) == 1
        assert requests[0].url == "https://doaj.org/api/search/articles/doi:10.1234%2Ftest.doi"
