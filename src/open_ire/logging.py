import logging
from typing import Any, Self

from scrapy.crawler import Crawler
from scrapy.logformatter import LogFormatter, LogFormatterResult
from scrapy.spiders import Spider


class OpenIRELogFormatter(LogFormatter):
    """Scrapy log formatter with optional dropped-item suppression."""

    def dropped(
        self,
        item: Any,
        exception: BaseException,
        response: Any,
        spider: Spider,
    ) -> LogFormatterResult:
        result = super().dropped(item, exception, response, spider)
        show_item = spider.crawler.settings.getbool("OPEN_IRE_LOG_DROPPED_ITEMS", True)
        if show_item:
            return result
        return {
            "level": result["level"],
            "msg": "Dropped: %(exception)s",
            "args": {"exception": exception},
        }


class OpenIRELogger:
    """Clamp noisy logger trees, and keep the console alive when logging to a file.

    All records propagate to the root logger, where Scrapy installs its single
    handler (stderr, or LOG_FILE when set) at LOG_LEVEL. Scrapy leaves the root
    logger itself at NOTSET, so LOG_LEVEL alone decides how verbose open_ire.*
    is; OPEN_IRE_LOGGER_LEVELS only exists to clamp trees *below* it. Scrapy
    already clamps several (see its DEFAULT_LOGGING), so an entry is worth
    adding only for a logger Scrapy doesn't cover.
    """

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> Self:
        log_levels: dict[str, str] = crawler.settings.getdict("OPEN_IRE_LOGGER_LEVELS", {})

        for logger_name, level_name in log_levels.items():
            logging.getLogger(logger_name).setLevel(str(level_name).upper())

        # Scrapy *replaces* the console handler with a file handler when
        # LOG_FILE is set; add a console handler back so both get the logs.
        # LOG_ENABLED=False means "no console output", which Scrapy itself
        # ignores once LOG_FILE is set -- so honor it here.
        if crawler.settings.get("LOG_FILE") and crawler.settings.getbool("LOG_ENABLED", True):
            root = logging.getLogger()
            if not any(getattr(h, "open_ire_handler", False) for h in root.handlers):
                # Scrapy always defines these, so no fallbacks are needed.
                log_format = crawler.settings.get("LOG_FORMAT")
                date_format = crawler.settings.get("LOG_DATEFORMAT")
                handler = logging.StreamHandler()
                handler.setLevel(crawler.settings.get("LOG_LEVEL"))
                handler.setFormatter(logging.Formatter(log_format, date_format))
                handler.open_ire_handler = True  # type: ignore[attr-defined]
                root.addHandler(handler)

        return cls()
