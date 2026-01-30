import json
import uuid
from collections.abc import Generator
from datetime import date
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from scrapy.http import HtmlResponse, Request
from sqlmodel import Session, SQLModel, create_engine, select

from open_ire.enums import OAEvidenceKind, DepositStatus, DepositTransitionReason
from open_ire.models import Article, ArticleOAEvidence, ArticleDepositStatusTransition
from open_ire.spiders.oa_license import OALicenseSpider


@pytest.fixture
def spider() -> Generator[OALicenseSpider, None, None]:
    with patch.object(OALicenseSpider, "logger", new_callable=lambda: MagicMock()):
        spider_instance = OALicenseSpider()
        yield spider_instance


@pytest.fixture
def spider_with_db(spider: OALicenseSpider) -> Generator[OALicenseSpider, None, None]:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    spider.engine = engine
    yield spider
    engine.dispose()


@pytest.fixture
def article_id() -> uuid.UUID:
    return uuid.UUID("12345678-1234-5678-1234-567812345678")


@pytest.fixture
def sample_article(spider_with_db: OALicenseSpider, article_id: uuid.UUID) -> Article:
    with Session(spider_with_db.engine) as session:
        article = Article(
            id=article_id,
            title="Test Article",
            authors="Test Author",
            publication_date=date(2023, 1, 15),
            repository="test_repo",
            reference="TEST001",
            url="https://example.com/article",
            doi="10.1234/test.doi",
        )
        session.add(article)
        session.commit()
        session.refresh(article)
        return article


@pytest.fixture
def crossref_oa_response() -> dict[str, Any]:
    return {
        "message": {
            "license": [
                {
                    "URL": "https://creativecommons.org/licenses/by/4.0/",
                    "content-version": "ver",
                    "delay-in-days": 0,
                }
            ]
        }
    }


@pytest.fixture
def datacite_oa_response() -> dict[str, Any]:
    return {
        "data": {
            "attributes": {
                "rightsList": [
                    {
                        "rights": "Creative Commons Attribution 4.0 International",
                        "rightsUri": "https://creativecommons.org/licenses/by/4.0/legalcode",
                        "rightsIdentifier": "cc-by-4.0",
                        "rightsIdentifierScheme": "SPDX",
                    }
                ]
            }
        }
    }


class TestIsOALicense:
    @pytest.mark.parametrize(
        "license_url,expected",
        [
            ("https://creativecommons.org/licenses/by/4.0/", True),
            ("https://creativecommons.org/publicdomain/zero/1.0/", True),
            ("https://CREATIVECOMMONS.ORG/LICENSES/BY/4.0/", True),  # Case insensitive
            ("https://www.elsevier.com/tdm/userlicense/1.0/", False),
            ("", False),
            (None, False),
        ],
    )
    def test_is_oa_license(self, license_url: str | None, expected: bool) -> None:
        assert OALicenseSpider.is_oa_license(license_url) == expected


class TestExtractCrossrefLicense:
    def test_extract_oa_license(self) -> None:
        licenses = [
            {
                "URL": "https://creativecommons.org/licenses/by/4.0/",
                "content-version": "ver",
                "delay-in-days": 0,
            }
        ]

        result = OALicenseSpider.extract_crossref_license(licenses)

        assert result["supports_oa"] is True
        assert result["license_urls"] == ["https://creativecommons.org/licenses/by/4.0/"]
        assert result["license_details"][0]["content_version"] == "ver"

    def test_extract_non_oa_license(self) -> None:
        licenses = [
            {
                "URL": "https://www.elsevier.com/tdm/userlicense/1.0/",
                "content-version": "tdm",
            }
        ]

        result = OALicenseSpider.extract_crossref_license(licenses)

        assert result["supports_oa"] is False


class TestExtractDataciteLicense:
    def test_extract_oa_license(self) -> None:
        rights_list = [
            {
                "rights": "Creative Commons Attribution 4.0 International",
                "rightsUri": "https://creativecommons.org/licenses/by/4.0/legalcode",
                "rightsIdentifier": "cc-by-4.0",
                "rightsIdentifierScheme": "SPDX",
            }
        ]

        result = OALicenseSpider.extract_datacite_license(rights_list)

        assert result["supports_oa"] is True
        assert result["license_details"][0]["name"] == "Creative Commons Attribution 4.0 International"
        assert result["license_details"][0]["identifier"] == "cc-by-4.0"

    def test_extract_non_oa_license(self) -> None:
        rights_list = [
            {
                "rights": "All rights reserved",
                "rightsUri": "https://example.com/proprietary",
            }
        ]

        result = OALicenseSpider.extract_datacite_license(rights_list)

        assert result["supports_oa"] is False


class TestLicenseSchemaConsistency:
    def test_output_structure_consistency(self) -> None:
        """Both extractors should produce the same output structure."""
        crossref_licenses = [
            {"URL": "https://creativecommons.org/licenses/by/4.0/", "content-version": "ver"}
        ]
        datacite_rights = [
            {"rightsUri": "https://creativecommons.org/licenses/by/4.0/", "rights": "CC BY 4.0"}
        ]

        crossref_result = OALicenseSpider.extract_crossref_license(crossref_licenses)
        datacite_result = OALicenseSpider.extract_datacite_license(datacite_rights)

        assert set(crossref_result.keys()) == set(datacite_result.keys())
        assert set(crossref_result.keys()) == {"license_details", "license_urls", "supports_oa"}
        assert crossref_result["supports_oa"] == datacite_result["supports_oa"]


class TestParseCrossrefLicense:
    def test_parse_crossref_with_license(
        self, spider: OALicenseSpider, article_id: uuid.UUID, crossref_oa_response: dict[str, Any]
    ) -> None:
        spider.save_license_evidence = MagicMock()  # type: ignore[method-assign]

        response = HtmlResponse(
            url="https://api.crossref.org/works/10.1234/test",
            body=json.dumps(crossref_oa_response).encode(),
            encoding="utf-8",
        )

        result = spider.parse_crossref(response, article_id, "10.1234/test")

        assert result is None
        spider.save_license_evidence.assert_called_once()
        call_args = spider.save_license_evidence.call_args
        assert call_args[0][0] == article_id
        assert call_args[0][2] == "crossref"

    def test_parse_crossref_falls_back_to_datacite(
        self, spider: OALicenseSpider, article_id: uuid.UUID
    ) -> None:
        """Test fallback to DataCite when Crossref has no license."""
        response_data: dict[str, Any] = {"message": {"license": []}}
        response = HtmlResponse(
            url="https://api.crossref.org/works/10.1234/test",
            body=json.dumps(response_data).encode(),
            encoding="utf-8",
        )

        result = spider.parse_crossref(response, article_id, "10.1234/test")

        assert isinstance(result, Request)
        assert spider.datacite_base_url in result.url


class TestParseDataciteLicense:
    def test_parse_datacite_with_license(
        self, spider: OALicenseSpider, article_id: uuid.UUID, datacite_oa_response: dict[str, Any]
    ) -> None:
        spider.save_license_evidence = MagicMock()  # type: ignore[method-assign]

        response = HtmlResponse(
            url="https://api.datacite.org/dois/10.1234/test",
            body=json.dumps(datacite_oa_response).encode(),
            encoding="utf-8",
        )

        spider.parse_datacite(response, article_id, "10.1234/test")

        spider.save_license_evidence.assert_called_once()
        call_args = spider.save_license_evidence.call_args
        assert call_args[0][2] == "datacite"

    def test_parse_datacite_no_license(
        self, spider: OALicenseSpider, article_id: uuid.UUID
    ) -> None:
        spider.save_license_evidence = MagicMock()  # type: ignore[method-assign]
        response_data: dict[str, Any] = {"data": {"attributes": {"rightsList": []}}}

        response = HtmlResponse(
            url="https://api.datacite.org/dois/10.1234/test",
            body=json.dumps(response_data).encode(),
            encoding="utf-8",
        )

        spider.parse_datacite(response, article_id, "10.1234/test")

        spider.save_license_evidence.assert_not_called()


class TestSaveLicenseEvidence:
    def test_save_oa_license_creates_transition(
        self, spider_with_db: OALicenseSpider, sample_article: Article
    ) -> None:
        license_data = {
            "supports_oa": True,
            "license_urls": ["https://creativecommons.org/licenses/by/4.0/"],
            "license_details": [{"url": "https://creativecommons.org/licenses/by/4.0/"}],
        }

        spider_with_db.save_license_evidence(
            sample_article.id, "10.1234/test", "crossref", license_data
        )

        with Session(spider_with_db.engine) as session:
            # Check evidence was saved
            evidence = session.exec(
                select(ArticleOAEvidence).where(
                    ArticleOAEvidence.article_id == sample_article.id
                )
            ).first()
            assert evidence is not None
            assert evidence.kind == OAEvidenceKind.LICENSE
            assert evidence.supports_oa is True
            assert evidence.source == "crossref"

            # Check transition was created
            transition = session.exec(
                select(ArticleDepositStatusTransition).where(
                    ArticleDepositStatusTransition.article_id == sample_article.id
                )
            ).first()
            assert transition is not None
            assert transition.to_status == DepositStatus.READY
            assert DepositTransitionReason.LICENSE_OA in transition.reasons

    def test_save_non_oa_license_no_transition(
        self, spider_with_db: OALicenseSpider, sample_article: Article
    ) -> None:
        """Test that non-OA license creates evidence but NO transition."""
        license_data = {
            "supports_oa": False,
            "license_urls": ["https://example.com/proprietary"],
            "license_details": [{"url": "https://example.com/proprietary"}],
        }

        spider_with_db.save_license_evidence(
            sample_article.id, "10.1234/test", "datacite", license_data
        )

        with Session(spider_with_db.engine) as session:
            # Check evidence was saved
            evidence = session.exec(
                select(ArticleOAEvidence).where(
                    ArticleOAEvidence.article_id == sample_article.id
                )
            ).first()
            assert evidence is not None
            assert evidence.supports_oa is False
            assert evidence.source == "datacite"

            # Check NO transition was created
            transition = session.exec(
                select(ArticleDepositStatusTransition).where(
                    ArticleDepositStatusTransition.article_id == sample_article.id
                )
            ).first()
            assert transition is None
