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


class DatabaseRevisionError(OpenIRError, RuntimeError):
    """Raised when an existing database is not at the latest migration revision."""

    def __init__(self, db_path: str, revision: str | None, head: str | None) -> None:
        found = revision or "unversioned (no alembic_version row)"
        message = (
            f"Database '{db_path}' is at migration revision {found}, but this code expects {head}. "
            f'Back up the file, then run: `OPEN_IRE_DATABASE_FILE="{db_path}" pixi run -e dev alembic upgrade head`. '
            f'See "Database Migrations" in CONTRIBUTING.md for more details.'
        )
        super().__init__(message)
        self.db_path = db_path
        self.revision = revision
        self.head = head
