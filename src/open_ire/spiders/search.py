import abc
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from scrapy import Spider
from scrapy.http import Request

from open_ire.author import AuthorIndex, AuthorRecord
from open_ire.settings import OPEN_IRE_DEFAULT_TERMS


class SearchSpider(Spider, metaclass=abc.ABCMeta):
    """
    An abstract base spider that iterates over a list of search terms.

    Subclasses must define how `self.search_terms` is populated in their `__init__`
    and must implement `build_search_request`.
    """

    search_terms: list[str]

    @staticmethod
    def _join_authors(values: list[str]) -> str | None:
        """Join a list of author names with semicolons, or return None if empty."""
        return "; ".join(values) if values else None

    async def start(self) -> AsyncIterator[Request]:
        for term in self.search_terms:
            if not term:
                continue
            yield self.build_search_request(term)

    @abc.abstractmethod
    def build_search_request(self, term: str) -> Request:
        """Build a Scrapy Request for a given search term."""
        raise NotImplementedError


class TermSearchSpider(SearchSpider):
    """
    A base spider that generates search requests from a list of terms
    provided via the 'terms' argument.
    """

    def __init__(self, terms: str | None = None, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

        if not terms:
            self.logger.info(
                "No 'terms' provided; using default terms: %s",
                OPEN_IRE_DEFAULT_TERMS,
            )
            terms = OPEN_IRE_DEFAULT_TERMS

        self.search_terms = [term.strip() for term in (terms or "").split(",")]


class AuthorSearchSpider(SearchSpider):
    """
    A specialized base spider that only searches using an author CSV file.

    This spider requires the `author_csv` argument. Subclasses can override
    `_get_author_name` to specify the required name format for the target API.
    """

    def __init__(self, author_csv: str | None = None, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if not author_csv:
            msg = f"The '{self.name}' spider requires the 'author_csv' argument."
            raise ValueError(msg)

        self.search_terms = self._get_search_terms(author_csv)

    def _get_author_name(self, record: AuthorRecord) -> str:
        """Return the author name in the default 'Firstname Lastname' format."""
        return f"{record.first_name} {record.last_name}"

    def _get_search_terms(self, author_csv: str) -> list[str]:
        author_path = Path(author_csv).resolve()
        author_index = AuthorIndex(author_path)
        return [self._get_author_name(record) for record in author_index.records]
