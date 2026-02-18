"""Common utility functions."""

import logging
from datetime import date, datetime
from typing import Any

from dateutil.parser import parse

logger = logging.getLogger(__name__)


def parse_date(value: Any) -> date | None:
    """Parse a date value into a date object, returning None on failure."""
    if not value:
        return None
    try:
        # Value containing just YYYY will produce YYYY-01-01; if unspecified, default defaults to today's date.
        january_first = datetime.today().replace(day=1, month=1)
        return parse(str(value), default=january_first).date()
    except (ValueError, TypeError):
        logger.warning("Can't parse date '%s'", value)
        return None


def validate_year(raw_year: str, field_name: str) -> int:
    """Validate and convert a year string to integer, raising ValueError on failure."""
    try:
        year = int(raw_year)
        if 1900 <= year <= 2100:  # Reasonable year range
            return year
        msg = f"Year {year} is outside reasonable range (1900-2100)"
        raise ValueError(msg)
    except (TypeError, ValueError) as e:
        msg = f"Invalid {field_name}: '{raw_year}' - {e}"
        raise ValueError(msg) from e


def as_list(value: Any) -> list[Any]:
    """Convert a value to a list, handling API's inconsistent list/single item responses."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]
