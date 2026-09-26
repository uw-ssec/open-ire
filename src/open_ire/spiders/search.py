import abc
from abc import ABC
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from scrapy import Spider
from scrapy.http import Request

from open_ire.author import AuthorIndex, ParsedAuthor
from open_ire.items import AuthorItem
from open_ire.settings import OPEN_IRE_DEFAULT_TERMS

type StartItem = Request | AuthorItem


class SearchSpider[TSearchPhrase](Spider, metaclass=abc.ABCMeta):
    """
    An abstract base spider that iterates over a list of search terms.

    Subclasses must define how `self.search_phrases` is populated in their
    `__init__` and must implement `build_search_request`.
    """

    search_phrases: list[TSearchPhrase]

    async def start(self) -> AsyncIterator[StartItem]:
        for phrase in self.search_phrases:
            if not phrase:
                continue
            yield self.build_search_request(phrase)

    @abc.abstractmethod
    def build_search_request(self, phrase: TSearchPhrase) -> Request:
        """Build a Scrapy Request for a given search term."""
        raise NotImplementedError


class TermSearchSpider(SearchSpider[str], ABC):
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

        self.search_phrases = [term.strip() for term in (terms or "").split(",")]

    @abc.abstractmethod
    def build_search_request(self, term: str) -> Request:
        """Build a Scrapy Request for a given search term."""
        raise NotImplementedError


class AuthorSearchSpider(SearchSpider[ParsedAuthor], ABC):
    """
    A specialized base spider that searches using author names from CSV file and/or individual author names.

    This spider accepts an `authors` argument that is auto-detected as either a CSV file path or a
    personal name, so callers no longer need to pick the right argument name. The legacy `author_csv`
    and `author_name` arguments are still accepted for backward compatibility. If several sources are
    provided, all of their authors are searched.

    Subclasses can override `author_name_for_query` to specify the required name format for the target API.
    """

    def __init__(
        self,
        authors: str | None = None,
        author_csv: str | None = None,
        author_name: str | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)

        authors = authors.strip() if authors else None
        author_csv = author_csv.strip() if author_csv else None
        author_name = author_name.strip() if author_name else None
        if not authors and not author_csv and not author_name:
            msg = (
                f"The '{self.name}' spider requires the 'authors' argument "
                "(a CSV file path or a personal name)."
            )
            raise ValueError(msg)

        self.search_phrases = []

        if authors:
            self._add_authors_argument(authors)

        if author_csv:
            self._add_authors_from_csv(author_csv)

        if author_name:
            self.search_phrases.append(ParsedAuthor(author_name))

    def _add_authors_argument(self, value: str) -> None:
        """Route a single `authors` value to CSV loading or name parsing based on its shape."""
        if self._looks_like_path(value):
            if not Path(value).expanduser().resolve().is_file():
                msg = (
                    f"The 'authors' argument '{value}' looks like a file path but does not exist. "
                    "Provide an existing CSV file or a personal name."
                )
                raise ValueError(msg)
            self._add_authors_from_csv(value)
        else:
            self.search_phrases.append(ParsedAuthor(value))

    def _add_authors_from_csv(self, csv_path: str) -> None:
        author_index = AuthorIndex(Path(csv_path).expanduser().resolve())
        self.search_phrases.extend(author_index.records)

    @staticmethod
    def _looks_like_path(value: str) -> bool:
        """Return True if the value is shaped like a file path rather than a personal name."""
        return value.lower().endswith(".csv") or "/" in value or "\\" in value

    async def start(self) -> AsyncIterator[Request | AuthorItem]:
        for author in self.search_phrases:
            yield AuthorItem(
                author=author,
                identifiers=[{"authority": "email", "identifier": author.email}]
                if author.email
                else [],
            )
        for author in self.search_phrases:
            if not author:
                continue
            yield self.build_search_request(author)

    @abc.abstractmethod
    def build_search_request(self, record: ParsedAuthor) -> Request:
        """Build a Scrapy Request for a given author record."""
        raise NotImplementedError

    @abc.abstractmethod
    def author_name_for_query(self, record: ParsedAuthor) -> str:
        """Return the author name in the required format for the target API."""
        raise NotImplementedError

    @staticmethod
    def canonical_author_name(record: ParsedAuthor) -> str:
        """Return the canonical name for the author record."""
        return record.canonical_name
