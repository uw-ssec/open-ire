import json
import uuid
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import quote

from scrapy import Spider
from scrapy.http import Request, Response
from sqlalchemy import Engine
from sqlmodel import Session, create_engine, select

from open_ire.enums import DepositStatus, DepositTransitionReason, OAEvidenceKind
from open_ire.models import Article, ArticleDepositStatusTransition, ArticleOAEvidence
from open_ire.pipelines import DOINormalizationPipeline
from open_ire.settings import OPEN_IRE_CONTACT_EMAIL


class LogMessages:
    ARTICLE_NOT_FOUND = "Article %s not found in database"
    ARTICLE_TRANSITIONED = "Article %s transitioned to READY based on OA license from %s"
    ARTICLES_FOUND = "Found %d articles with DOIs"
    FALLBACK_TO_DATACITE = "Crossref lookup failed for DOI %s, trying DataCite: %s"
    INVALID_JSON = "Invalid JSON from %s for DOI %s"
    LICENSE_EVIDENCE_SAVED = "Saved license evidence for article %s from %s (supports_oa=%s)"
    NO_DATABASE_ENGINE = "No database engine available"
    NO_LICENSE_FOUND = "No license information found for DOI %s"
    NO_LICENSE_FOUND_WITH_DETAILS = "No license information found for DOI %s (%s)"
    NO_LICENSE_IN_CROSSREF = "No license info in Crossref for DOI %s, trying DataCite"
    SKIPPING_ARTICLE = "Skipping article %s - already has license evidence"


class OALicenseSpider(Spider):
    """
    Fetch license information from Crossref and DataCite APIs.
    This spider queries the database for articles with DOIs, then:
    1. Requests license info from Crossref API.
    2. Falls back to DataCite API if Crossref has no license info.
    3. Saves evidence to ArticleOAEvidence.
    4. Creates ArticleOAStatusTransition if license indicates open access.
    """

    name = "oa_license"
    crossref_base_url = "https://api.crossref.org/works"
    datacite_base_url = "https://api.datacite.org/dois"

    custom_settings = {  # noqa: RUF012
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 2.0,
        "DOWNLOAD_DELAY": 1,
        "ITEM_PIPELINES": {},
        "ROBOTSTXT_OBEY": False,
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.engine: Engine | None = None
        self.contact_email = OPEN_IRE_CONTACT_EMAIL
        self.request_headers = {
            "User-Agent": f"mailto:{OPEN_IRE_CONTACT_EMAIL}",
            "Accept": "application/json",
        }

    @staticmethod
    def is_oa_license(license_url: str | None) -> bool:
        if not license_url:
            return False

        oa_license_patterns = [
            "creativecommons.org/licenses/",
            "creativecommons.org/publicdomain/",
            "opendatacommons.org/licenses/",
            "opensource.org/licenses/",
            "rightsstatements.org/vocab/InC-EDU",  # Educational Use
            "rightsstatements.org/vocab/NoC",  # No Copyright
        ]

        license_lower = license_url.lower()
        return any(pattern in license_lower for pattern in oa_license_patterns)

    @staticmethod
    def extract_crossref_license(licenses: list[dict[str, Any]]) -> dict[str, Any]:
        """Extract and normalize license data from Crossref response.

        Crossref license structure:
        {
            "URL": "https://creativecommons.org/licenses/by/4.0/",
            "start": {"date-parts": [[2020, 1, 1]]},
            "delay-in-days": 0,
            "content-version": "am" (Accepted Manuscript) or "tdm" (Text/Data Mining)
        }
        """
        license_urls: list[str] = []
        license_details: list[dict[str, Any]] = []

        for lic in licenses:
            url = lic.get("URL", "")
            if url:
                license_urls.append(url)

            license_details.append(
                {
                    "url": url,
                    "content_version": lic.get("content-version"),
                    "delay_in_days": lic.get("delay-in-days"),
                }
            )

        supports_oa = any(OALicenseSpider.is_oa_license(url) for url in license_urls)

        return {
            "license_details": license_details,
            "license_urls": license_urls,
            "supports_oa": supports_oa,
        }

    @staticmethod
    def extract_datacite_license(rights_list: list[dict[str, Any]]) -> dict[str, Any]:
        """Extract and normalize license data from DataCite response.

        DataCite rights structure:
        {
            "rights": "Creative Commons Attribution 4.0 International",
            "rightsUri": "https://creativecommons.org/licenses/by/4.0/legalcode",
            "rightsIdentifier": "cc-by-4.0",
            "rightsIdentifierScheme": "SPDX",
            "schemeUri": "https://spdx.org/licenses/"
        }
        """
        license_urls: list[str] = []
        license_details: list[dict[str, Any]] = []

        for rights in rights_list:
            url = rights.get("rightsUri", "") or rights.get("rightsURI", "")
            if url:
                license_urls.append(url)

            license_details.append(
                {
                    "url": url,
                    "name": rights.get("rights"),
                    "identifier": rights.get("rightsIdentifier"),
                    "identifier_scheme": rights.get("rightsIdentifierScheme"),
                }
            )

        supports_oa = any(OALicenseSpider.is_oa_license(url) for url in license_urls)

        return {
            "license_details": license_details,
            "license_urls": license_urls,
            "supports_oa": supports_oa,
        }

    @classmethod
    def from_crawler(cls, crawler: Any, *args: Any, **kwargs: Any) -> "OALicenseSpider":
        spider = super().from_crawler(crawler, *args, **kwargs)
        db_path = crawler.settings.get("OPEN_IRE_DATABASE_FILE")
        if db_path:
            spider.engine = create_engine(
                f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
            )

        return spider

    def closed(self, reason: str) -> None:  # noqa: ARG002
        """Clean up database engine when spider closes."""
        if self.engine:
            self.engine.dispose()

    def query_articles_with_doi(self) -> list[tuple[uuid.UUID, str]]:
        if not self.engine:
            return []

        with Session(self.engine) as session:
            statement = select(Article.id, Article.doi).where(
                Article.doi.isnot(None),  # type: ignore[union-attr]
                Article.doi != "",
            )
            results = session.exec(statement).all()
            return [(row[0], row[1]) for row in results if row[1]]

    def has_license_evidence(self, article_id: uuid.UUID) -> bool:
        if not self.engine:
            return False

        with Session(self.engine) as session:
            statement = select(ArticleOAEvidence).where(
                ArticleOAEvidence.article_id == article_id,
                ArticleOAEvidence.kind == OAEvidenceKind.LICENSE,
                ArticleOAEvidence.source.in_(["crossref", "datacite"]),  # type: ignore[union-attr]
            )
            return session.exec(statement).first() is not None

    async def start(self) -> AsyncIterator[Request]:
        articles = self.query_articles_with_doi()
        self.logger.info(LogMessages.ARTICLES_FOUND, len(articles))

        for article_id, doi in articles:
            if self.has_license_evidence(article_id):
                self.logger.debug(LogMessages.SKIPPING_ARTICLE, article_id)
                continue

            normalized_doi = DOINormalizationPipeline.normalize(doi)
            if not normalized_doi:
                continue

            yield self.build_crossref_request(article_id, normalized_doi)

    def build_crossref_request(self, article_id: uuid.UUID, doi: str) -> Request:
        encoded_doi = quote(doi, safe="")
        url = f"{self.crossref_base_url}/{encoded_doi}"

        return Request(
            url,
            headers=self.request_headers,
            callback=self.parse_crossref,
            errback=self.handle_crossref_error,
            cb_kwargs={"article_id": article_id, "doi": doi},
            dont_filter=True,
        )

    def build_datacite_request(self, article_id: uuid.UUID, doi: str) -> Request:
        encoded_doi = quote(doi, safe="")
        url = f"{self.datacite_base_url}/{encoded_doi}"

        return Request(
            url,
            headers=self.request_headers,
            callback=self.parse_datacite,
            errback=self.handle_datacite_error,
            cb_kwargs={"article_id": article_id, "doi": doi},
            dont_filter=True,
        )

    def parse_crossref(self, response: Response, article_id: uuid.UUID, doi: str) -> Request | None:
        if response.status != 200:
            self.logger.debug(LogMessages.FALLBACK_TO_DATACITE, doi, f"status {response.status}")
            return self.build_datacite_request(article_id, doi)

        try:
            data = json.loads(response.text or "{}")
        except json.JSONDecodeError:
            self.logger.warning(LogMessages.INVALID_JSON, "Crossref", doi)
            return self.build_datacite_request(article_id, doi)

        message = data.get("message", {})
        licenses = message.get("license", [])

        if not licenses:
            self.logger.debug(LogMessages.NO_LICENSE_IN_CROSSREF, doi)
            return self.build_datacite_request(article_id, doi)

        license_data = self.extract_crossref_license(licenses)
        self.save_license_evidence(article_id, doi, "crossref", license_data)

        return None

    def handle_crossref_error(self, failure: Any) -> Request | None:
        request = failure.request
        article_id = request.cb_kwargs.get("article_id")
        doi = request.cb_kwargs.get("doi")

        self.logger.debug(LogMessages.FALLBACK_TO_DATACITE, doi, str(failure.value))

        if article_id and doi:
            return self.build_datacite_request(article_id, doi)

        return None

    def parse_datacite(self, response: Response, article_id: uuid.UUID, doi: str) -> None:
        if response.status != 200:
            self.logger.warning(
                LogMessages.NO_LICENSE_FOUND_WITH_DETAILS,
                doi,
                f"DataCite status: {response.status}",
            )
            return

        try:
            data = json.loads(response.text or "{}")
        except json.JSONDecodeError:
            self.logger.warning(LogMessages.INVALID_JSON, "DataCite", doi)
            return

        attributes = data.get("data", {}).get("attributes", {})
        rights_list = attributes.get("rightsList", [])

        if not rights_list:
            self.logger.warning(LogMessages.NO_LICENSE_FOUND, doi)
            return

        license_data = self.extract_datacite_license(rights_list)
        self.save_license_evidence(article_id, doi, "datacite", license_data)

    def handle_datacite_error(self, failure: Any) -> None:
        request = failure.request
        doi = request.cb_kwargs.get("doi")

        self.logger.warning(
            LogMessages.NO_LICENSE_FOUND_WITH_DETAILS, doi, f"DataCite error: {failure.value}"
        )

    def save_license_evidence(
        self,
        article_id: uuid.UUID,
        doi: str,
        source: str,
        license_data: dict[str, Any],
    ) -> None:
        if not self.engine:
            self.logger.error(LogMessages.NO_DATABASE_ENGINE)
            return

        supports_oa = license_data.get("supports_oa", False)

        with Session(self.engine) as session:
            article = session.get(Article, article_id)
            if not article:
                self.logger.warning(LogMessages.ARTICLE_NOT_FOUND, article_id)
                return

            current_status = article.deposit_status
            evidence = ArticleOAEvidence(
                article_id=article_id,
                kind=OAEvidenceKind.LICENSE,
                supports_oa=supports_oa,
                source=source,
                data={
                    "doi": doi,
                    "license_urls": license_data.get("license_urls", []),
                    "license_details": license_data.get("license_details", []),
                },
            )
            session.add(evidence)

            if supports_oa and current_status not in (DepositStatus.READY, DepositStatus.PUBLISHED):
                transition = ArticleDepositStatusTransition(
                    article_id=article_id,
                    from_status=current_status,
                    to_status=DepositStatus.READY,
                    reasons=[DepositTransitionReason.LICENSE_OA],
                )
                session.add(transition)

                self.logger.info(LogMessages.ARTICLE_TRANSITIONED, article_id, source)

            session.commit()

            self.logger.info(LogMessages.LICENSE_EVIDENCE_SAVED, article_id, source, supports_oa)
