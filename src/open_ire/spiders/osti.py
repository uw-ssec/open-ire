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
        # OSTI's robots.txt only restricts Googlebot; /servlets/purl/ (fulltext
        # PDFs) is unrestricted for all other user-agents. Scrapy's global
        # ROBOTSTXT_OBEY=True was incorrectly applying Googlebot rules as a
        # fallback and blocking PDF downloads that OSTI explicitly allows.
        "ROBOTSTXT_OBEY": False,
        # Fulltext PDFs are hosted behind publisher CloudFront WAFs that block
        # the default self-identifying user-agent for open-access content OSTI
        # links to. We use a browser-shaped UA that *still* identifies the
        # project and a contact address, scoped to this spider only so the
        # honest default in base.py is unchanged. NOTE: under discussion in
        # PR #119 — whether we should circumvent these WAFs at all.
        "USER_AGENT": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36 "
            "(open_ire; +https://lib.uw.edu/; contact uwtextmine@uw.edu)"
        ),
    }

    def build_search_request(self, term: str) -> Request:
        """Build the first-page API request for *term*."""
        self.logger.info("Searching OSTI for %r (page_size=%d)", term, self.page_size)
        return self._build_page_request(term, page=1)

    def _build_page_request(self, term: str, page: int) -> Request:
        """Build an API request for *term* at the given *page*."""
        params = {
            "q": f'"{term}"',
            "has_fulltext": "true",
            "rows": str(self.page_size),
            "page": str(page),
        }
        url = f"{self.api_url}?{urlencode(params)}"
        return Request(
            url,
            callback=self.parse,
            headers={"Accept": "application/json"},
            meta={"search_term": term, "page": page},
        )

    # === RESPONSE PARSING ===

    def parse(self, response: Response, **kwargs: Any) -> Generator[Request | ArticleItem]:  # noqa: ARG002
        """Parse a page of JSON results and follow pagination."""
        search_term: str = response.meta["search_term"]
        current_page: int = response.meta["page"]
        records: list[dict[str, Any]] = json.loads(response.text or "[]")

        self.logger.info(
            "OSTI returned %d record(s) for %r (page %d)",
            len(records),
            search_term,
            current_page,
        )

        if not records:
            return

        yielded = 0
        for record in records:
            item = self._parse_record(record)
            if item is not None:
                yielded += 1
                yield item

        if yielded < len(records):
            self.logger.info(
                "Skipped %d record(s) on page %d for %r (missing title/id or fulltext)",
                len(records) - yielded,
                current_page,
                search_term,
            )

        # Follow pagination: if we got a full page, request the next one.
        if len(records) >= self.page_size:
            next_page = current_page + 1
            self.logger.debug("Requesting next page %d for %r", next_page, search_term)
            yield self._build_page_request(search_term, page=next_page)

    # === RECORD PARSING ===

    def _parse_record(self, record: dict[str, Any]) -> ArticleItem | None:
        """Convert a single OSTI API record dict into an :class:`ArticleItem`."""
        title = (record.get("title") or "").strip()
        osti_id = str(record.get("osti_id", "")).strip()

        if not title or not osti_id:
            self.logger.debug(
                "Skipping record with missing title or osti_id: %s",
                record.get("osti_id", "<unknown>"),
            )
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

        for key in ("subjects", "sponsor_orgs", "research_orgs"):
            if value := record.get(key):
                extra[key] = value

        return extra
