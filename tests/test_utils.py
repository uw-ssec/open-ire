"""Tests for utils.py module."""

from datetime import date

import pytest

from open_ire.utils import as_list, parse_date, validate_year


class TestParseDate:
    """Tests for parse_date function."""

    def test_parse_valid_date(self):
        """Test parsing valid date strings."""
        assert parse_date("2023-01-15") == date(2023, 1, 15)
        assert parse_date("January 15, 2023") == date(2023, 1, 15)

    def test_parse_none_or_empty(self):
        """Test parsing None or empty values."""
        assert parse_date(None) is None
        assert parse_date("") is None

    def test_parse_invalid_date(self):
        """Test parsing invalid date strings."""
        assert parse_date("not a date") is None
        assert parse_date("2023-13-45") is None


class TestValidateYear:
    """Tests for validate_year function."""

    def test_valid_year(self):
        """Test validating valid years."""
        assert validate_year("2023", "test_field") == 2023
        assert validate_year("1950", "test_field") == 1950

    def test_invalid_year_string(self):
        """Test validating invalid year strings."""
        with pytest.raises(ValueError, match="Invalid test_field"):
            validate_year("not a year", "test_field")

    def test_year_out_of_range(self):
        """Test validating years outside reasonable range."""
        with pytest.raises(ValueError, match="outside reasonable range"):
            validate_year("1800", "test_field")
        with pytest.raises(ValueError, match="outside reasonable range"):
            validate_year("2200", "test_field")


class TestAsList:
    """Tests for as_list function."""

    def test_none_value(self):
        """Test converting None to list."""
        assert as_list(None) == []

    def test_already_list(self):
        """Test that lists are returned as-is."""
        test_list = [1, 2, 3]
        assert as_list(test_list) is test_list

    def test_single_value(self):
        """Test converting single values to list."""
        assert as_list("single") == ["single"]
        assert as_list(42) == [42]
        assert as_list({"key": "value"}) == [{"key": "value"}]
