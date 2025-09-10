from typing import Any

import pytest

from open_ire.errors import SpiderParameterError
from open_ire.settings import OPEN_IRE_DEFAULT_TERMS
from open_ire.spiders.noaa import NOAASpider


class TestNOAASpider:
    def test_default_params(self):
        """Test spider initialization with default parameters."""
        spider = NOAASpider()
        assert spider.name == "noaa"
        assert spider.terms == OPEN_IRE_DEFAULT_TERMS.lower().split(",")

    def test_custom_params(self):
        """Test spider initialization with custom parameters."""
        terms = "unittest,test,sample"
        spider = NOAASpider(terms=terms)
        assert spider.terms == ["unittest", "test", "sample"]

    def test_page_param_not_supported(self):
        """Test that page parameter raises ValueError."""
        with pytest.raises(SpiderParameterError) as e:
            NOAASpider(page="2")

        assert e.value.parameter == "page"
        assert e.value.spider_name == NOAASpider.name


    def test_normalize_terms(self):
        """Test term normalization."""
        terms = "Unittest, Test , Sample,  "
        normalized = NOAASpider._normalize_terms(terms)
        assert normalized == ["unittest", "test", "sample"]

    def test_get_field_value(self):
        """Test field value extraction."""
        doc = {
            "field1": "value1",
            "field2": ["value2a", "value2b"],
            "field3": None,
        }

        assert NOAASpider._get_field_value(doc, ["missing", "field1"]) == "value1"
        assert NOAASpider._get_field_value(doc, ["field2"]) == ['value2a', 'value2b']
        assert NOAASpider._get_field_value(doc, ["missing"]) is None

    def test_normalize_field_value(self):
        """Test field value normalization."""
        assert NOAASpider._normalize_field_value("unittest") == "unittest"
        assert NOAASpider._normalize_field_value(["first", "second"]) == "first"
        assert NOAASpider._normalize_field_value([]) is None
        assert NOAASpider._normalize_field_value(None) is None
        assert NOAASpider._normalize_field_value("  spaced  ") == "spaced"

    def test_extract_authors(self):
        """Test author extraction."""
        spider = NOAASpider()

        # Single author
        doc: dict[str, Any] = {"mods.name_personal": "Unittest Author"}
        assert spider._extract_authors(doc) == "Unittest Author"

        # Multiple authors
        doc = {"mods.name_personal": ["Unittest Author 1", "Unittest Author 2"]}
        assert spider._extract_authors(doc) == "Unittest Author 1, Unittest Author 2"

        # No authors
        doc = {}
        assert spider._extract_authors(doc) is None

    def test_extract_publication_date(self):
        """Test publication date extraction."""
        spider = NOAASpider()

        # Valid date
        doc = {"mods.ss_publishyear": "2025"}
        result = spider._extract_publication_date(doc)
        assert result is not None
        assert result.year == 2025

        # Invalid date
        doc = {"mods.ss_publishyear": "invalid"}
        assert spider._extract_publication_date(doc) is None

        # No date
        doc = {}
        assert spider._extract_publication_date(doc) is None

    def test_extract_keywords(self):
        """Test keyword extraction."""

        keywords = NOAASpider._extract_keywords(["unittest", "test", "unittest"])
        assert len(keywords) == 2
        assert "unittest" in keywords
        assert "test" in keywords

        # Single keyword
        keywords = NOAASpider._extract_keywords("sample")
        assert keywords == ["sample"]

        # Empty
        keywords = NOAASpider._extract_keywords("")
        assert keywords == []

    def test_extract_extra_details(self):
        """Test extra details extraction."""
        spider = NOAASpider()
        doc = {
            "mods.sm_publisher": "Unittest Publications",
            "mods.volume": "Volume 1",
            "mods.subject_topic": ["unittest", "test"],
        }

        extra = spider._extract_extra_details(doc)
        assert extra["publisher"] == "Unittest Publications"
        assert extra["volume"] == "Volume 1"
        assert "unittest" in extra["keywords"]
        assert "test" in extra["keywords"]

    def test_document_matches_terms(self):
        """Test document term matching."""
        spider = NOAASpider(terms="unittest,test")

        # Matching document
        doc = {
            "mods.title": "Unittest Sample Study",
            "mods.abstract": "This study examines unittest patterns.",
        }
        assert spider._document_matches_terms(doc)

        # Non-matching document
        doc = {
            "mods.title": "Sample Study",
            "mods.abstract": "This study examines sample practices.",
        }
        assert not spider._document_matches_terms(doc)

        # No terms (should match all)
        spider_no_terms = NOAASpider(terms="")
        assert spider_no_terms._document_matches_terms(doc)

    def test_build_searchable_text(self):
        """Test searchable text building."""
        spider = NOAASpider()
        doc = {
            "mods.title": "Unittest Study",
            "mods.abstract": "Test Sample Analysis",
            "mods.name_personal": ["Unittest Author"],
            "missing_field": "Should not be included",
        }

        searchable = spider._build_searchable_text(doc)
        assert "unittest study" in searchable
        assert "test sample analysis" in searchable
        assert "unittest author" in searchable
        assert "should not be included" not in searchable

    def test_create_article_item(self):
        """Test article item creation."""
        spider = NOAASpider()
        doc = {
            "PID": "noaa:12345",
            "mods.title": "Unittest Article",
            "mods.abstract": "Unittest abstract",
            "mods.sm_digital_object_identifier": "10.1000/unittest",
            "mods.name_personal": "Unittest Author",
            "mods.ss_publishyear": "2025",
            "mods.sm_issn": "1234-5678",
            "mods.sm_publisher": "Unittest Publisher",
        }

        item = spider._create_article_item(doc)

        assert item.reference == "12345"
        assert item.title == "Unittest Article"
        assert item.abstract == "Unittest abstract"
        assert item.doi == "10.1000/unittest"
        assert item.authors == "Unittest Author"
        assert item.publication_date.year == 2025
        assert item.issn == "1234-5678"
        assert item.repository == "noaa"
        assert item.url == "https://repository.library.noaa.gov/view/noaa/12345"
        assert len(item.file_urls) == 1
        assert "noaa_12345_DS1.pdf" in item.file_urls[0]
        assert item.extra["publisher"] == "Unittest Publisher"
