import logging
from typing import Self

from scrapy.crawler import Crawler


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

        return cls(level_name)
