"""Tests for logging.py module."""

import logging
from collections.abc import Generator
from typing import cast
from unittest.mock import MagicMock

import pytest
from scrapy.crawler import Crawler
from scrapy.settings import Settings

from open_ire.logging import OpenIRELogger


class TestOpenIRELogger:
    """Tests the OpenIRELogger clamping of logger levels and its console handler."""

    @pytest.fixture
    def crawler(self) -> Crawler:
        """Create a mock crawler for testing."""
        mock_crawler = MagicMock(spec=Crawler)
        mock_crawler.settings = Settings()

        return cast(Crawler, mock_crawler)

    @pytest.fixture(autouse=True)
    def _restore_logger_levels(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Keep level changes from leaking into other tests."""
        for name in ("open_ire", "scrapy"):
            monkeypatch.setattr(logging.getLogger(name), "level", logging.NOTSET)

    @pytest.fixture
    def root_logger(self) -> Generator[logging.Logger, None, None]:
        """Yield the root logger, removing any handler a test adds to it."""
        root = logging.getLogger()
        existing = list(root.handlers)
        yield root
        for handler in root.handlers:
            if handler not in existing:
                root.removeHandler(handler)

    @staticmethod
    def _added_handlers(root: logging.Logger) -> list[logging.Handler]:
        return [h for h in root.handlers if getattr(h, "open_ire_handler", False)]

    def test_named_loggers_are_clamped_to_their_configured_levels(self, crawler: Crawler) -> None:
        crawler.settings.set("OPEN_IRE_LOGGER_LEVELS", {"scrapy": "WARNING"})

        OpenIRELogger.from_crawler(crawler)

        assert logging.getLogger("scrapy").level == logging.WARNING

    def test_unlisted_loggers_are_left_to_inherit_the_root_logger(
        self, crawler: Crawler, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """LOG_LEVEL gates the handler, so open_ire.* needs no level of its own."""
        # install_scrapy_root_handler() puts the root logger at NOTSET, which is what
        # lets an unset logger emit everything and leaves filtering to the handler.
        monkeypatch.setattr(logging.root, "level", logging.NOTSET)
        crawler.settings.set("OPEN_IRE_LOGGER_LEVELS", {"scrapy": "WARNING"})

        OpenIRELogger.from_crawler(crawler)

        assert logging.getLogger("open_ire").level == logging.NOTSET
        assert logging.getLogger("open_ire").isEnabledFor(logging.DEBUG)

    def test_unknown_level_name_is_rejected(self, crawler: Crawler) -> None:
        """A typo in the setting must fail loudly rather than pick a default."""
        crawler.settings.set("OPEN_IRE_LOGGER_LEVELS", {"scrapy": "WANRING"})

        with pytest.raises(ValueError, match="WANRING"):
            OpenIRELogger.from_crawler(crawler)

    def test_console_handler_is_added_alongside_the_log_file(
        self, crawler: Crawler, root_logger: logging.Logger
    ) -> None:
        crawler.settings.set("LOG_FILE", "output/test.log")
        crawler.settings.set("LOG_LEVEL", "DEBUG")

        OpenIRELogger.from_crawler(crawler)

        added = self._added_handlers(root_logger)
        assert len(added) == 1
        assert added[0].level == logging.DEBUG

    def test_console_handler_is_added_only_once(
        self, crawler: Crawler, root_logger: logging.Logger
    ) -> None:
        crawler.settings.set("LOG_FILE", "output/test.log")

        OpenIRELogger.from_crawler(crawler)
        OpenIRELogger.from_crawler(crawler)

        assert len(self._added_handlers(root_logger)) == 1

    def test_console_handler_is_skipped_when_logging_is_disabled(
        self, crawler: Crawler, root_logger: logging.Logger
    ) -> None:
        crawler.settings.set("LOG_FILE", "output/test.log")
        crawler.settings.set("LOG_ENABLED", False)

        OpenIRELogger.from_crawler(crawler)

        assert self._added_handlers(root_logger) == []

    def test_no_console_handler_without_a_log_file(
        self, crawler: Crawler, root_logger: logging.Logger
    ) -> None:
        OpenIRELogger.from_crawler(crawler)

        assert self._added_handlers(root_logger) == []
