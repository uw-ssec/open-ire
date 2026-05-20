"""Spider to collect DOE-funded research articles from OSTI.GOV.

This spider queries the OSTI.GOV public API for articles matching
configurable search terms and yields one :class:`~open_ire.items.ArticleItem`
per record that has full text available.

API documentation: https://www.osti.gov/api/v1/docs

Usage::

    pixi run scrapy crawl osti
    pixi run scrapy crawl osti -a terms="university of washington"
"""

import json
import re
from collections.abc import Generator
from typing import Any
from urllib.parse import urlencode

from scrapy.http import Request, Response

from open_ire.author import ParsedAuthor
from open_ire.items import ArticleItem
from open_ire.spiders.search import TermSearchSpider
from open_ire.utils import parse_date

# Matches "[affiliation text]" or "[affiliation text" (unclosed) in an author name.
_AFFILIATION_RE = re.compile(r"\s*\[.*")
# Matches "(ORCID:digits)" at the end of an author name.
_ORCID_RE = re.compile(r"\s*\(ORCID:\d+\)\s*$")


class OstiSpider(TermSearchSpider):
    """Collect DOE-funded research articles from the OSTI.GOV API.

    For each search term, queries ``/api/v1/records`` with
    ``has_fulltext=true`` and paginates through all results, yielding
    an :class:`ArticleItem` per record.
    """

    name = "osti"
    api_url = "https://www.osti.gov/api/v1/records"
    page_size = 100

    custom_settings = {  # noqa: RUF012
        "DOWNLOAD_DELAY": 1,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 4,
    }

    def build_search_request(self, term: str) -> Request:
        """Build the first-page API request for *term*."""
        params = {
            "q": f'"{term}"',
            "has_fulltext": "true",
            "rows": str(self.page_size),
            "page": "1",
        }
        url = f"{self.api_url}?{urlencode(params)}"
        return Request(
            url,
            callback=self.parse,
            headers={"Accept": "application/json"},
            meta={"search_term": term},
        )

    # === RESPONSE PARSING ===

    def parse(self, response: Response, **kwargs: Any) -> Generator[Request | ArticleItem]:  # noqa: ARG002
        """Parse a page of JSON results and follow pagination."""
        records: list[dict[str, Any]] = json.loads(response.text or "[]")

        if not records:
            return

        for record in records:
            item = self._parse_record(record)
            if item is not None:
                yield item

        # Follow pagination: if we got a full page, request the next one.
        if len(records) >= self.page_size:
            search_term = response.meta.get("search_term", "")
            next_page = self._current_page(response) + 1
            params = {
                "q": f'"{search_term}"',
                "has_fulltext": "true",
                "rows": str(self.page_size),
                "page": str(next_page),
            }
            url = f"{self.api_url}?{urlencode(params)}"
            yield Request(
                url,
                callback=self.parse,
                headers={"Accept": "application/json"},
                meta={"search_term": search_term},
            )

    # === RECORD PARSING ===

    def _parse_record(self, record: dict[str, Any]) -> ArticleItem | None:
        """Convert a single OSTI API record dict into an :class:`ArticleItem`."""
        title = (record.get("title") or "").strip()
        osti_id = str(record.get("osti_id", "")).strip()

        if not title or not osti_id:
            return None

        return ArticleItem(
            abstract=(record.get("description") or "").strip() or None,
            authors=self._extract_authors(record),
            doi=self._extract_doi(record),
            extra=self._build_extra(record),
            file_urls=self._extract_fulltext_urls(record),
            issn=self._extract_issn(record),
            publication_date=parse_date(record.get("publication_date")),
            reference=osti_id,
            repository=self.name,
            title=title,
            url=self._extract_citation_url(record) or f"https://www.osti.gov/biblio/{osti_id}",
        )

    # === FIELD EXTRACTION HELPERS ===

    @staticmethod
    def _extract_authors(record: dict[str, Any]) -> str | None:
        """Clean and encode the author list from an OSTI record.

        OSTI returns authors as ``"Last, First [Institution]
        (ORCID:digits)"``.  We strip the bracketed affiliation and
        ORCID suffix so the result matches :class:`ParsedAuthor`
        conventions.
        """
        raw_authors: list[str] = record.get("authors") or []
        if not raw_authors:
            return None

        cleaned: list[ParsedAuthor] = []
        for raw in raw_authors:
            name = _ORCID_RE.sub("", raw)
            name = _AFFILIATION_RE.sub("", name).strip()
            if name:
                cleaned.append(ParsedAuthor(name))

        return ParsedAuthor.encode_author_string(cleaned) if cleaned else None

    @staticmethod
    def _extract_doi(record: dict[str, Any]) -> str | None:
        """Extract and normalise the DOI, stripping the URL prefix."""
        doi = (record.get("doi") or "").strip()
        if not doi:
            return None
        # OSTI returns DOIs as full URLs: https://doi.org/10.xxxx/...
        for prefix in ("https://doi.org/", "http://doi.org/"):
            if doi.lower().startswith(prefix):
                doi = doi[len(prefix) :]
                break
        return doi or None

    @staticmethod
    def _extract_issn(record: dict[str, Any]) -> str | None:
        """Extract the ISSN, stripping the ``ISSN`` label prefix."""
        raw = (record.get("journal_issn") or "").strip()
        if raw.upper().startswith("ISSN"):
            raw = raw[4:].strip()
        return raw or None

    @staticmethod
    def _extract_fulltext_urls(record: dict[str, Any]) -> list[str]:
        """Return URLs for fulltext links from the record's ``links`` array."""
        links: list[dict[str, str]] = record.get("links") or []
        return [
            link["href"] for link in links if link.get("rel") == "fulltext" and link.get("href")
        ]

    @staticmethod
    def _extract_citation_url(record: dict[str, Any]) -> str | None:
        """Return the OSTI citation (biblio) URL."""
        links: list[dict[str, str]] = record.get("links") or []
        for link in links:
            if link.get("rel") == "citation" and link.get("href"):
                return link["href"]
        return None

    @staticmethod
    def _build_extra(record: dict[str, Any]) -> dict[str, Any]:
        """Collect supplementary metadata into the ``extra`` dict."""
        extra: dict[str, Any] = {}
        for key in (
            "journal_name",
            "journal_volume",
            "journal_issue",
            "publisher",
            "product_type",
        ):
            if value := (record.get(key) or "").strip():
                extra[key] = value

        if subjects := record.get("subjects"):
            extra["subjects"] = subjects
        if sponsor_orgs := record.get("sponsor_orgs"):
            extra["sponsor_orgs"] = sponsor_orgs
        if research_orgs := record.get("research_orgs"):
            extra["research_orgs"] = research_orgs

        return extra

    # === UTILITIES ===

    @staticmethod
    def _current_page(response: Response) -> int:
        """Extract the current page number from the request URL."""
        url = response.url
        if "page=" in url:
            for part in url.split("&"):
                if part.startswith("page=") or part.split("?")[-1].startswith("page="):
                    try:
                        return int(part.split("=")[-1])
                    except ValueError:
                        pass
        return 1
