"""Tests for author.py module."""

from pathlib import Path

import pytest
from nameparser import HumanName

from open_ire.author import AuthorIndex, ParsedAuthor


class TestParsedAuthor:
    """Tests for ParsedAuthor class."""

    def test_create_with_string(self) -> None:
        """Test creating ParsedAuthor with a string name."""
        record = ParsedAuthor(name="John A. Doe Jr.", email="john@example.com")

        assert record.email == "john@example.com"
        assert record.first_name == "John"
        assert record.last_name == "Doe"
        assert record.middle_name == "A."
        assert record.suffix == "Jr."

    def test_create_with_human_name(self) -> None:
        """Test creating ParsedAuthor with a HumanName object."""
        human_name = HumanName("Dr. Jane Smith")
        record = ParsedAuthor(name=human_name, email="jane@example.com")

        assert record.email == "jane@example.com"
        assert record.first_name == "Jane"
        assert record.last_name == "Smith"
        assert record.title == "Dr."

    def test_first_initial(self) -> None:
        """Test first_initial property."""
        record = ParsedAuthor(name="John Doe", email="test@example.com")
        assert record.first_initial == "J"

    def test_middle_initial(self) -> None:
        """Test middle_initial property."""
        record = ParsedAuthor(name="John Andrew Doe", email="test@example.com")
        assert record.middle_initial == "A"

    def test_middle_initial_empty(self) -> None:
        """Test middle_initial when no middle name exists."""
        record = ParsedAuthor(name="John Doe", email="test@example.com")
        assert record.middle_initial == ""

    def test_complex_name_parsing(self) -> None:
        """Test parsing of complex names with titles and suffixes."""
        record = ParsedAuthor(name="Prof. Robert James Smith III, PhD", email="rsmith@example.com")

        assert record.title == "Prof."
        assert record.first_name == "Robert"
        assert record.middle_name == "James"
        assert record.last_name == "Smith"
        assert record.suffix == "III, PhD"

    def test_empty_name_components(self) -> None:
        """Test that empty name components return empty strings."""
        record = ParsedAuthor(name="", email="test@example.com")

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


class TestLikelySame:
    """Test the likely_same() method for fuzzy author matching."""

    def test_exact_match_with_middle_names(self):
        """Exact matches should return True."""
        author1 = ParsedAuthor("Welland, Sasha Su-Ling")
        author2 = ParsedAuthor("Welland, Sasha Su-Ling")
        assert author1.likely_same(author2)

    def test_one_has_middle_name_other_does_not(self):
        """One author with middle name, other without -> likely same person."""
        author1 = ParsedAuthor("Welland, Sasha")
        author2 = ParsedAuthor("Welland, Sasha Su-Ling")
        assert author1.likely_same(author2)
        assert author2.likely_same(author1)  # Symmetric

    def test_middle_name_is_prefix(self):
        """Middle name that is a prefix of another -> likely same person."""
        author1 = ParsedAuthor("Welland, Sasha Su")
        author2 = ParsedAuthor("Welland, Sasha Su-Ling")
        assert author1.likely_same(author2)
        assert author2.likely_same(author1)  # Symmetric

    def test_different_middle_names(self):
        """Different middle names that aren't prefixes -> likely different people."""
        author1 = ParsedAuthor("Welland, Sasha Su-Ling")
        author2 = ParsedAuthor("Welland, Sasha Mary")
        assert not author1.likely_same(author2)

    def test_different_last_names(self):
        """Different last names -> different people."""
        author1 = ParsedAuthor("Welland, Sasha")
        author2 = ParsedAuthor("Smith, Sasha")
        assert not author1.likely_same(author2)

    def test_different_first_names(self):
        """Different first names -> different people."""
        author1 = ParsedAuthor("Welland, Sasha", "sasha@example.com")
        author2 = ParsedAuthor("Welland, Mary", "mary@example.com")
        assert not author1.likely_same(author2)

    def test_same_name_different_emails(self):
        """Same name but different emails -> different people."""
        author1 = ParsedAuthor("Welland, Sasha", "sasha1@example.com")
        author2 = ParsedAuthor("Welland, Sasha", "sasha2@example.com")
        assert not author1.likely_same(author2)

    def test_same_name_one_email_missing(self):
        """Same name, one email missing -> likely same person."""
        author1 = ParsedAuthor("Welland, Sasha Su-Ling", "sasha@example.com")
        author2 = ParsedAuthor("Welland, Sasha Su-Ling", None)
        assert author1.likely_same(author2)
        assert author2.likely_same(author1)  # Symmetric

    def test_case_insensitive_matching(self):
        """Matching should be case-insensitive."""
        author1 = ParsedAuthor("WELLAND, SASHA", "SASHA@EXAMPLE.COM")
        author2 = ParsedAuthor("welland, sasha", "sasha@example.com")
        assert author1.likely_same(author2)

    def test_middle_name_with_spaces(self):
        """Middle names with multiple parts should work."""
        author1 = ParsedAuthor("Smith, John Paul")
        author2 = ParsedAuthor("Smith, John Paul George")
        assert author1.likely_same(author2)

    def test_both_no_middle_names(self):
        """Both authors without middle names -> likely same person."""
        author1 = ParsedAuthor("Welland, Sasha")
        author2 = ParsedAuthor("Welland, Sasha")
        assert author1.likely_same(author2)

    def test_both_no_emails(self):
        """Both authors without emails but matching names -> likely same person."""
        author1 = ParsedAuthor("Welland, Sasha Su-Ling", None)
        author2 = ParsedAuthor("Welland, Sasha", None)
        assert author1.likely_same(author2)

    def test_middle_initial_vs_full_middle_name(self):
        """Middle initial vs full middle name -> likely same person."""
        author1 = ParsedAuthor("Welland, Sasha S")
        author2 = ParsedAuthor("Welland, Sasha Su-Ling")
        assert author1.likely_same(author2)

    def test_different_middle_initials(self):
        """Different middle initials -> likely different people."""
        author1 = ParsedAuthor("Welland, Sasha S", "sasha1@example.com")
        author2 = ParsedAuthor("Welland, Sasha M", "sasha2@example.com")
        assert not author1.likely_same(author2)

    def test_diacritics_ignored(self):
        """Names with and without diacritics should match (ASCII folding)."""
        author1 = ParsedAuthor("Tarragó, David")
        author2 = ParsedAuthor("Tarrago, David")
        assert author1.likely_same(author2)

    def test_various_diacritics(self):
        """Various diacritics should be normalized."""
        author1 = ParsedAuthor("Müller, José")
        author2 = ParsedAuthor("Muller, Jose")
        assert author1.likely_same(author2)

    def test_hyphenated_names(self):
        """Hyphenated names should match with or without hyphens."""
        author1 = ParsedAuthor("Welland, Sasha Su-Ling")
        author2 = ParsedAuthor("Welland, Sasha Su Ling")
        assert author1.likely_same(author2)

    def test_apostrophes_in_names(self):
        """Names with apostrophes should match without them."""
        author1 = ParsedAuthor("O'Brien, Patrick")
        author2 = ParsedAuthor("OBrien, Patrick")
        assert author1.likely_same(author2)

    def test_first_initial_vs_full_first_name(self):
        """First initial vs full first name -> likely same person."""
        author1 = ParsedAuthor("Welland, S.")
        author2 = ParsedAuthor("Welland, Sasha")
        assert author1.likely_same(author2)
        assert author2.likely_same(author1)

    def test_first_initial_matches_different_names_same_letter(self):
        """First initial should match any name starting with that letter."""
        author_j = ParsedAuthor("Smith, J.")
        author_john = ParsedAuthor("Smith, John")
        author_jane = ParsedAuthor("Smith, Jane")

        assert author_j.likely_same(author_john)
        assert author_j.likely_same(author_jane)
        assert author_john.likely_same(author_j)
        assert author_jane.likely_same(author_j)

    def test_different_first_initials(self):
        """Different first initials -> different people."""
        author1 = ParsedAuthor("Smith, J.")
        author2 = ParsedAuthor("Smith, K.")
        assert not author1.likely_same(author2)

    def test_first_initial_with_middle_name(self):
        """First initial with middle name should still match."""
        author1 = ParsedAuthor("Welland, S. Su-Ling")
        author2 = ParsedAuthor("Welland, Sasha Su-Ling")
        assert author1.likely_same(author2)
        assert author2.likely_same(author1)

    def test_first_initial_different_letter(self):
        """First initial with different starting letter -> different people."""
        author1 = ParsedAuthor("Welland, S.")
        author2 = ParsedAuthor("Welland, Mary")
        assert not author1.likely_same(author2)

    def test_hyphenated_first_name_with_initial(self):
        """Hyphenated first name should match its initial."""
        author1 = ParsedAuthor("Welland, Su-Ling")
        author2 = ParsedAuthor("Welland, S.")
        assert author1.likely_same(author2)
        assert author2.likely_same(author1)
