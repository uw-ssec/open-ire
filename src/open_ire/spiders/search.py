import abc
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from scrapy import Spider
from scrapy.http import Request

from open_ire.author import AuthorIndex, ParsedAuthor
from open_ire.settings import OPEN_IRE_DEFAULT_TERMS


class SearchSpider(Spider, metaclass=abc.ABCMeta):
    """
    An abstract base spider that iterates over a list of search terms.

    Subclasses must define how `self.search_terms` is populated in their `__init__`
    and must implement `build_search_request`.
    """

    search_terms: list[str]

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
    A specialized base spider that searches using author names from CSV file and/or individual author names.

    This spider accepts `author_csv` and/or `author_name` arguments. If both are provided, the individual
    author name is added to the list from the CSV. Subclasses can override `_get_author_name` to specify
    the required name format for the target API.
    """

    def __init__(
        self,
        author_csv: str | None = None,
        author_name: str | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)

        if not author_csv and not author_name:
            msg = f"The '{self.name}' spider requires either the 'author_csv' or 'author_name' argument (or both)."
            raise ValueError(msg)

        # Start with empty list
        self.search_terms = []

        # Add authors from CSV if provided
        if author_csv:
            self.search_terms.extend(self._get_search_terms(author_csv))

        # Add individual author name if provided
        if author_name:
            self.search_terms.append(author_name.strip())

    def _get_author_name(self, record: ParsedAuthor) -> str:
        """Return the author name in the default 'Firstname Lastname' format."""
        return f"{record.first_name} {record.last_name}"

    def _get_search_terms(self, author_csv: str) -> list[str]:
        author_path = Path(author_csv).resolve()
        author_index = AuthorIndex(author_path)
        return [self._get_author_name(record) for record in author_index.records]
