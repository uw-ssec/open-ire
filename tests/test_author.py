"""Tests for author.py module."""

from pathlib import Path

import pytest
from nameparser import HumanName

from open_ire.author import AuthorIndex, AuthorRecord


class TestAuthorRecord:
    """Tests for AuthorRecord class."""

    def test_create_with_string(self) -> None:
        """Test creating AuthorRecord with a string name."""
        record = AuthorRecord(name="John A. Doe Jr.", email="john@example.com")

        assert record.email == "john@example.com"
        assert record.first_name == "John"
        assert record.last_name == "Doe"
        assert record.middle_name == "A."
        assert record.suffix == "Jr."

    def test_create_with_human_name(self) -> None:
        """Test creating AuthorRecord with a HumanName object."""
        human_name = HumanName("Dr. Jane Smith")
        record = AuthorRecord(name=human_name, email="jane@example.com")

        assert record.email == "jane@example.com"
        assert record.first_name == "Jane"
        assert record.last_name == "Smith"
        assert record.title == "Dr."

    def test_first_initial(self) -> None:
        """Test first_initial property."""
        record = AuthorRecord(name="John Doe", email="test@example.com")
        assert record.first_initial == "J"

    def test_middle_initial(self) -> None:
        """Test middle_initial property."""
        record = AuthorRecord(name="John Andrew Doe", email="test@example.com")
        assert record.middle_initial == "A"

    def test_middle_initial_empty(self) -> None:
        """Test middle_initial when no middle name exists."""
        record = AuthorRecord(name="John Doe", email="test@example.com")
        assert record.middle_initial == ""

    def test_complex_name_parsing(self) -> None:
        """Test parsing of complex names with titles and suffixes."""
        record = AuthorRecord(name="Prof. Robert James Smith III, PhD", email="rsmith@example.com")

        assert record.title == "Prof."
        assert record.first_name == "Robert"
        assert record.middle_name == "James"
        assert record.last_name == "Smith"
        assert record.suffix == "III, PhD"

    def test_empty_name_components(self) -> None:
        """Test that empty name components return empty strings."""
        record = AuthorRecord(name="", email="test@example.com")

        assert record.first_name == ""
        assert record.last_name == ""
        assert record.middle_name == ""
        assert record.title == ""
        assert record.suffix == ""
        assert record.first_initial == ""
        assert record.middle_initial == ""


class TestAuthorIndex:
    """Tests for AuthorIndex class."""

    def test_load_valid_csv(self, tmp_path: Path) -> None:
        """Test loading a valid CSV file."""
        csv_content = """FirstName,LastName,Email
John,Doe,john@example.com
Jane,Smith,jane@example.com
"""
        csv_path = tmp_path / "authors.csv"
        csv_path.write_text(csv_content)

        index = AuthorIndex(csv_path)

        assert len(index.records) == 2
        assert index.records[0].first_name == "John"
        assert index.records[0].last_name == "Doe"
        assert index.records[0].email == "john@example.com"
        assert index.records[1].first_name == "Jane"
        assert index.records[1].last_name == "Smith"

    def test_missing_file(self, tmp_path: Path) -> None:
        """Test that FileNotFoundError is raised for missing file."""
        csv_path = tmp_path / "nonexistent.csv"

        with pytest.raises(FileNotFoundError, match="Author file not found"):
            AuthorIndex(csv_path)

    def test_missing_required_columns(self, tmp_path: Path) -> None:
        """Test that ValueError is raised when required columns are missing."""
        csv_content = """Name,Email
John Doe,john@example.com
"""
        csv_path = tmp_path / "authors.csv"
        csv_path.write_text(csv_content)

        with pytest.raises(ValueError, match="must include columns"):
            AuthorIndex(csv_path)

    def test_empty_csv(self, tmp_path: Path) -> None:
        """Test that ValueError is raised for CSV with no valid records."""
        csv_content = """FirstName,LastName,Email
,,
"""
        csv_path = tmp_path / "authors.csv"
        csv_path.write_text(csv_content)

        with pytest.raises(ValueError, match="No valid author records found"):
            AuthorIndex(csv_path)

    def test_skip_invalid_rows(self, tmp_path: Path) -> None:
        """Test that invalid rows are skipped."""
        csv_content = """FirstName,LastName,Email
John,Doe,john@example.com
,,
Jane,Smith,jane@example.com
"""
        csv_path = tmp_path / "authors.csv"
        csv_path.write_text(csv_content)

        index = AuthorIndex(csv_path)

        assert len(index.records) == 2
        assert index.records[0].first_name == "John"
        assert index.records[1].first_name == "Jane"

    def test_whitespace_handling(self, tmp_path: Path) -> None:
        """Test that whitespace is properly stripped."""
        csv_content = """FirstName,LastName,Email
  John  ,  Doe  ,  john@example.com
"""
        csv_path = tmp_path / "authors.csv"
        csv_path.write_text(csv_content)

        index = AuthorIndex(csv_path)

        assert index.records[0].first_name == "John"
        assert index.records[0].last_name == "Doe"
        assert index.records[0].email == "john@example.com"

    def test_utf8_bom_handling(self, tmp_path: Path) -> None:
        """Test that UTF-8 BOM is handled correctly."""
        csv_content = """FirstName,LastName,Email
John,Doe,john@example.com
"""
        csv_path = tmp_path / "authors.csv"
        csv_path.write_text(csv_content, encoding="utf-8-sig")

        index = AuthorIndex(csv_path)

        assert len(index.records) == 1
        assert index.records[0].first_name == "John"
