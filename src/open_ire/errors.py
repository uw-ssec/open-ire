from scrapy.exceptions import DropItem


class OpenIRError(Exception):
    """Base class for all Open IRE custom exceptions."""


class SpiderParameterError(OpenIRError, ValueError):
    """Raised when a spider receives a parameter that it does not support."""

    def __init__(self, parameter: str, spider_name: str) -> None:
        message = f"The {spider_name} spider does not support {parameter} parameter"
        super().__init__(message)
        self.parameter = parameter
        self.spider_name = spider_name


class DuplicateItemError(OpenIRError, DropItem):
    """Raised when a duplicate item is found in a spider pipeline."""

    def __init__(self, reference: str, spider_name: str) -> None:
        message = f"Item ID already seen: {reference} by {spider_name} spider"
        super().__init__(message)
        self.reference = reference
        self.spider_name = spider_name


class ConfigurationError(OpenIRError, RuntimeError):
    """
    Raised when required Scrapy settings are missing or misconfigured.
    """

    def __init__(self, setting_name: str, source: str = "settings.py") -> None:
        message = f"{setting_name} must be set in {source}"
        super().__init__(message)
        self.setting_name = setting_name
        self.source = source


class DatabaseDuplicateItemError(OpenIRError, DropItem):
    """Raised when a duplicate row is detected at the database level."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or "Duplicate item found in database.")


class AmbiguousAuthorError(OpenIRError):
    """Raised when author disambiguation fails."""

    def __init__(self, author_name: str, candidate_count: int, reason: str) -> None:
        message = f"Cannot disambiguate '{author_name}': {reason} ({candidate_count} candidates)"
        super().__init__(message)
        self.author_name = author_name
        self.candidate_count = candidate_count
        self.reason = reason
