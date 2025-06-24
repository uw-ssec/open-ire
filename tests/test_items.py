from __future__ import annotations

import pytest

from open_ire.items import OpenIreItem


class TestOpenIreItem:
    @pytest.fixture
    def required_fields(self):
        """Return a dictionary with required fields for an OpenIreItem."""
        return {
            "authors": "Test Author",
            "file_urls": ["https://example.com/test.pdf"],
            "publication_date": "2025",
            "reference": "TEST123",
            "repository": "test",
            "title": "Test Title",
            "url": "https://example.com/test",
        }

    def test_init_with_required_fields(self, required_fields):
        """Test initialization with only required fields."""
        item = OpenIreItem(**required_fields)

        # Required fields
        assert item.authors == required_fields["authors"]
        assert item.file_urls == required_fields["file_urls"]
        assert item.publication_date == required_fields["publication_date"]
        assert item.reference == required_fields["reference"]
        assert item.repository == required_fields["repository"]
        assert item.title == required_fields["title"]
        assert item.url == required_fields["url"]

        # Optional fields
        assert item.abstract is None
        assert item.doi is None
        assert item.eissn is None
        assert item.files is None
        assert item.isbn is None
        assert item.issn is None

    def test_init_with_all_fields(self, required_fields):
        """Test initialization with all fields."""
        optional_fields = {
            "abstract": "Test abstract",
            "doi": "10.1234/test",
            "eissn": "1234-5678",
            "files": [{"path": "test.pdf", "url": "https://example.com/test.pdf"}],
            "isbn": "978-1234567890",
            "issn": "1234-5678",
        }

        all_fields = {**required_fields, **optional_fields}
        item = OpenIreItem(**all_fields)

        for field, value in all_fields.items():
            assert getattr(item, field) == value

    def test_dict_conversion(self, required_fields):
        """Test conversion to dictionary."""
        import dataclasses

        item = OpenIreItem(**required_fields)
        item_dict = dataclasses.asdict(item)

        assert set(item_dict.keys()) == {
            "authors",
            "file_urls",
            "publication_date",
            "reference",
            "repository",
            "title",
            "url",
            "abstract",
            "doi",
            "eissn",
            "files",
            "isbn",
            "issn",
        }

        for field, value in required_fields.items():
            assert item_dict[field] == value
