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
    """Capture Open IRE INFO/DEBUG output while preserving Scrapy/Twisted logs."""

    def __init__(self, level_name: str = "INFO") -> None:
        self.level_name = level_name

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> Self:
        level_name = crawler.settings.get("OPEN_IRE_LOG_LEVEL", "INFO")
        level = getattr(logging, str(level_name).upper(), logging.INFO)

        log_format = crawler.settings.get(
            "LOG_FORMAT", "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
        )
        date_format = crawler.settings.get("LOG_DATEFORMAT", "%Y-%m-%d %H:%M:%S")

        handler = logging.StreamHandler()
        handler.setLevel(level)
        handler.setFormatter(logging.Formatter(log_format, date_format))
        handler.open_ire_handler = True  # type: ignore[attr-defined]

        def attach(logger_name: str) -> None:
            logger = logging.getLogger(logger_name)
            if any(getattr(h, "open_ire_handler", False) for h in logger.handlers):
                return
            logger.setLevel(level)
            logger.addHandler(handler)
            logger.propagate = False

        # Capture module-level logs under open_ire.*
        attach("open_ire")
        # Capture spider logs emitted via self.logger (named after spider).
        if crawler.spider:
            attach(crawler.spider.name)

        # Override levels based on the OPEN_IRE_LOG_LEVELS setting
        log_levels: dict[str, str] = crawler.settings.getdict("OPEN_IRE_LOG_LEVELS", {})
        for logger_name, override_name in log_levels.items():
            override_level = getattr(logging, str(override_name).upper(), logging.INFO)
            logging.getLogger(logger_name).setLevel(override_level)

        return cls(level_name)
