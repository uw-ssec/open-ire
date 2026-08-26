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
    """Tune per-logger levels so Open IRE and Scrapy logs share Scrapy's handler.

    All records propagate to the root logger, where Scrapy installs its single
    handler (stderr, or LOG_FILE when set) at LOG_LEVEL. Noise control therefore
    happens at the *emitting* loggers: LOG_LEVEL must be permissive (DEBUG), and
    OPEN_IRE_LOG_LEVELS clamps noisy subtrees (e.g. "scrapy", "twisted").
    """

    def __init__(self, level_name: str = "INFO") -> None:
        self.level_name = level_name

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> Self:
        level_name = crawler.settings.get("OPEN_IRE_LOG_LEVEL", "INFO")
        level = getattr(logging, str(level_name).upper(), logging.INFO)

        # Module-level logs under open_ire.*
        logging.getLogger("open_ire").setLevel(level)
        # Spider logs emitted via self.logger (named after spider).
        if crawler.spider:
            logging.getLogger(crawler.spider.name).setLevel(level)

        # Override levels based on the OPEN_IRE_LOG_LEVELS setting
        log_levels: dict[str, str] = crawler.settings.getdict("OPEN_IRE_LOG_LEVELS", {})
        for logger_name, override_name in log_levels.items():
            override_level = getattr(logging, str(override_name).upper(), logging.INFO)
            logging.getLogger(logger_name).setLevel(override_level)

        # Scrapy *replaces* the console handler with a file handler when
        # LOG_FILE is set; add a console handler back so both get the logs.
        # LOG_ENABLED=False means "no console output", which Scrapy itself
        # ignores once LOG_FILE is set -- so honor it here.
        if crawler.settings.get("LOG_FILE") and crawler.settings.getbool("LOG_ENABLED", True):
            root = logging.getLogger()
            if not any(getattr(h, "open_ire_handler", False) for h in root.handlers):
                log_format = crawler.settings.get(
                    "LOG_FORMAT", "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
                )
                date_format = crawler.settings.get("LOG_DATEFORMAT", "%Y-%m-%d %H:%M:%S")
                handler = logging.StreamHandler()
                handler.setLevel(crawler.settings.get("LOG_LEVEL", "DEBUG"))
                handler.setFormatter(logging.Formatter(log_format, date_format))
                handler.open_ire_handler = True  # type: ignore[attr-defined]
                root.addHandler(handler)

        return cls(level_name)
