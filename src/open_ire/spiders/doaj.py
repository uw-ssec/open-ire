import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from scrapy.http import Request, Response
from sqlmodel import Session, col, select

from open_ire.enums import DepositTransitionReason, DepositWarrant
from open_ire.models import Article
from open_ire.pipelines import DOINormalizationPipeline
from open_ire.spiders.deposit_warrant import BaseDepositWarrantSpider


class LogMessages:
    ARTICLES_FOUND = "Found %d candidate articles for DOAJ lookup"
    WARRANT_SAVED = "Saved DOAJ warrant for article %s (supports_oa=%s, strategy=%s)"
    SEARCH_ERROR = "DOAJ lookup failed for article %s (%s)"
    SEARCH_STATUS_ERROR = "DOAJ returned status %s for article %s"
    SEARCH_INVALID_JSON = "DOAJ returned invalid JSON for article %s"
    SKIPPING_ARTICLE = "Skipping article %s with existing deposit warrant"


@dataclass(frozen=True, slots=True)
class _ArticleCandidate:
    article_id: uuid.UUID
    doi: str


class DOAJSpider(BaseDepositWarrantSpider):
    """Establish an EXTERNAL_OA deposit warrant from DOAJ using DOI article matching."""

    name = "doaj"
    base_url = "https://doaj.org/api"
    search_endpoint = "articles"
    match_strategy = "article_doi"

    @staticmethod
    def build_search_query(candidate: _ArticleCandidate) -> str:
        return f"doi:{candidate.doi}"

    @staticmethod
    def extract_bibjson(result: dict[str, Any]) -> dict[str, Any] | None:
        bibjson = result.get("bibjson", {})
        return bibjson if isinstance(bibjson, dict) else None

    def has_existing_warrant(self, article_id: uuid.UUID) -> bool:
        return self.has_any_deposit_warrant(article_id)

    def query_article_candidates(self) -> list[_ArticleCandidate]:
        if not self.engine:
            return []

        with Session(self.engine) as session:
            rows = session.exec(
                select(Article.id, Article.doi).where(
                    col(Article.doi).is_not(None),
                    col(Article.doi) != "",
                )
            ).all()

        candidates: list[_ArticleCandidate] = []
        for article_id, doi in rows:
            normalized_doi = DOINormalizationPipeline.normalize(doi)
            if not normalized_doi:
                continue

            candidates.append(_ArticleCandidate(article_id=article_id, doi=normalized_doi))

        return candidates

    def build_search_request(
        self,
        candidate: _ArticleCandidate,
    ) -> Request:
        query = self.build_search_query(candidate)
        encoded_query = quote(query, safe=":")
        url = f"{self.base_url}/search/{self.search_endpoint}/{encoded_query}"

        return Request(
            url,
            headers=self.request_headers,
            callback=self.parse_search_response,
            errback=self.handle_search_error,
            cb_kwargs={
                "candidate": candidate,
            },
            dont_filter=True,
        )

    def match_article_by_doi(
        self, results: list[dict[str, Any]], expected_doi: str
    ) -> dict[str, Any] | None:
        normalized_expected_doi = DOINormalizationPipeline.normalize(expected_doi)
        if not normalized_expected_doi:
            return None

        matches: list[dict[str, Any]] = []
        for result in results:
            bibjson = self.extract_bibjson(result)
            if not bibjson:
                continue

            identifiers = bibjson.get("identifier", [])
            if not isinstance(identifiers, list):
                continue

            normalized_result_dois: set[str] = set()
            for identifier in identifiers:
                if not isinstance(identifier, dict):
                    continue

                if identifier.get("type") != "doi":
                    continue

                normalized_doi = DOINormalizationPipeline.normalize(identifier.get("id"))
                if normalized_doi:
                    normalized_result_dois.add(normalized_doi)

            if normalized_expected_doi in normalized_result_dois:
                matches.append(result)

        if len(matches) != 1:
            return None

        match = matches[0]
        bibjson = self.extract_bibjson(match) or {}
        return {
            "supports_oa": True,
            "allow_transition": True,
            "confidence": "high",
            "match_record": {
                "id": match.get("id"),
                "title": bibjson.get("title"),
                "journal": bibjson.get("journal"),
                "identifier": bibjson.get("identifier"),
            },
        }

    def save_doaj_warrant(
        self,
        article_id: uuid.UUID,
        *,
        supports_oa: bool,
        allow_transition: bool,
        warrant_data: dict[str, Any],
    ) -> None:
        self.save_deposit_warrant(
            article_id,
            kind=DepositWarrant.EXTERNAL_OA,
            source="doaj",
            supports_oa=supports_oa,
            data=warrant_data,
            transition_reason=DepositTransitionReason.EXTERNAL_OA,
            allow_transition=allow_transition,
        )

    @classmethod
    def build_warrant_data(
        cls,
        candidate: _ArticleCandidate,
        attempts: list[dict[str, Any]],
        match_data: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "attempts": attempts,
            "matched": True,
            "match_strategy": cls.match_strategy,
            "confidence": match_data.get("confidence"),
            "match_record": match_data.get("match_record"),
            "article_input": {
                "doi": candidate.doi,
            },
        }

    def parse_search_response(
        self,
        response: Response,
        candidate: _ArticleCandidate,
    ) -> Request | None:
        query = self.build_search_query(candidate)

        if response.status != 200:
            self.logger.debug(
                LogMessages.SEARCH_STATUS_ERROR, response.status, candidate.article_id
            )
            return None

        try:
            payload = json.loads(response.text or "{}")
        except json.JSONDecodeError:
            self.logger.warning(LogMessages.SEARCH_INVALID_JSON, candidate.article_id)
            return None

        results_raw = payload.get("results", [])
        results = [result for result in results_raw if isinstance(result, dict)]
        attempts = [
            {
                "strategy": self.match_strategy,
                "endpoint": self.search_endpoint,
                "query": query,
                "status": response.status,
                "total": payload.get("total"),
                "results_count": len(results),
            }
        ]

        match_data = self.match_article_by_doi(results, candidate.doi)
        if not match_data:
            return None

        supports_oa = bool(match_data.get("supports_oa"))
        allow_transition = bool(match_data.get("allow_transition"))
        self.save_doaj_warrant(
            candidate.article_id,
            supports_oa=supports_oa,
            allow_transition=allow_transition,
            warrant_data=self.build_warrant_data(candidate, attempts, match_data),
        )
        self.logger.info(
            LogMessages.WARRANT_SAVED, candidate.article_id, supports_oa, self.match_strategy
        )
        return None

    def handle_search_error(self, failure: Any) -> None:
        request = failure.request
        candidate = request.cb_kwargs["candidate"]
        self.logger.debug(LogMessages.SEARCH_ERROR, candidate.article_id, failure.value)

    async def start(self) -> AsyncIterator[Request]:
        candidates = self.query_article_candidates()
        self.logger.info(LogMessages.ARTICLES_FOUND, len(candidates))

        for candidate in candidates:
            if self.has_existing_warrant(candidate.article_id):
                identifier = candidate.doi if candidate.doi else candidate.article_id
                self.logger.debug(LogMessages.SKIPPING_ARTICLE, identifier)
                continue

            yield self.build_search_request(candidate)
