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

from open_ire.affiliation import split_institutions, uw_affiliations
from open_ire.author import ParsedAuthor
from open_ire.items import ArticleItem
from open_ire.spiders.search import TermSearchSpider
from open_ire.utils import parse_date

# Matches "[affiliation text]" or "[affiliation text" (unclosed) in an author name.
_AFFILIATION_RE = re.compile(r"\s*\[.*")
# Captures the affiliation inside "Last, First [affiliation]", tolerating a
# missing closing bracket.
_AFFILIATION_CAPTURE_RE = re.compile(r"\[([^\]]*)\]?")
# Matches "(ORCID:digits)" at the end of an author name.
_ORCID_RE = re.compile(r"\s*\(ORCID:\d+\)\s*$")


class OstiSpider(TermSearchSpider):
    """Collect DOE-funded research articles from the OSTI.GOV API.

    For each search term, queries ``/api/v1/records`` with
    ``has_fulltext=true`` and paginates through all results, yielding
    an :class:`ArticleItem` per record that is affiliated with UW.

    OSTI's ``q`` parameter searches the indexed full text of the PDF, not
    just its metadata, so it returns every report that so much as *mentions*
    "University of Washington" in its references or acknowledgements.  On a
    sample of 600 live results only 32% had any UW affiliation at all.  Each
    record is therefore checked against its own structured affiliation
    fields, and one without UW among them is dropped before it reaches the
    pipelines -- see :meth:`_uw_affiliations`.

    The broad query is kept deliberately: 44% of the records that pass the
    check name UW *only* in an author's affiliation, and OSTI's ``author:``
    field index does not cover affiliation text, so a narrower
    ``research_org:`` query would silently lose them.
    """

    name = "osti"
    api_url = "https://www.osti.gov/api/v1/records"
    page_size = 100

    #: Set ``OPEN_IRE_REQUIRE_UW_AFFILIATION=False`` to keep every record the
    #: API returns, e.g. to re-measure the false-positive rate.
    require_uw_affiliation = True

    custom_settings = {  # noqa: RUF012
        "DOWNLOAD_DELAY": 1,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 4,
        # OSTI's robots.txt only restricts Googlebot; /servlets/purl/ (fulltext
        # PDFs) is unrestricted for all other user-agents. Scrapy's global
        # ROBOTSTXT_OBEY=True was incorrectly applying Googlebot rules as a
        # fallback and blocking PDF downloads that OSTI explicitly allows.
        "ROBOTSTXT_OBEY": False,
        # OSTI rate-limits bursts of fulltext PDF downloads with HTTP 503 or
        # dropped connections; waiting and retrying recovers them (verified
        # on 75/75 sampled failures). Retry those politely with exponential
        # backoff instead of the built-in immediate retries. Priority 560
        # keeps it above the built-in RetryMiddleware (550) so it handles
        # 429/503 responses first.
        "DOWNLOADER_MIDDLEWARES": {
            "open_ire.middlewares.BackoffRetryMiddleware": 560,
        },
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

        unaffiliated = 0
        unusable = 0
        for record in records:
            if self._require_uw_affiliation and not self._uw_affiliations(record):
                unaffiliated += 1
                self.logger.debug(
                    "Dropping OSTI record %s (%r): no UW affiliation, matched %r in full text only",
                    record.get("osti_id", "<unknown>"),
                    (record.get("title") or "")[:80],
                    search_term,
                )
                continue

            item = self._parse_record(record, search_term)
            if item is None:
                unusable += 1
                continue
            yield item

        if unaffiliated:
            self.logger.info(
                "Dropped %d of %d record(s) on page %d for %r: no UW affiliation",
                unaffiliated,
                len(records),
                current_page,
                search_term,
            )
        if unusable:
            self.logger.info(
                "Skipped %d record(s) on page %d for %r (missing title or osti_id)",
                unusable,
                current_page,
                search_term,
            )

        # Follow pagination: if we got a full page, request the next one.
        if len(records) >= self.page_size:
            next_page = current_page + 1
            self.logger.debug("Requesting next page %d for %r", next_page, search_term)
            yield self._build_page_request(search_term, page=next_page)

    # === AFFILIATION CHECKING ===

    @property
    def _require_uw_affiliation(self) -> bool:
        """Whether records without a UW affiliation should be dropped."""
        # `settings` is only attached once a crawler adopts the spider.
        settings = getattr(self, "settings", None)
        if settings is None:
            return self.require_uw_affiliation
        return bool(
            settings.getbool("OPEN_IRE_REQUIRE_UW_AFFILIATION", self.require_uw_affiliation)
        )

    @staticmethod
    def _record_affiliations(record: dict[str, Any]) -> list[str]:
        """Return every institution named in *record*'s structured fields.

        Covers the research and sponsoring organisations, plus the
        affiliation OSTI brackets into each author entry -- the only place a
        UW connection is recorded for a large minority of UW articles.  Fields
        naming several institutions at once are split into one entry each.
        """
        fields: list[str] = [
            *(record.get("research_orgs") or []),
            *(record.get("sponsor_orgs") or []),
        ]
        for author in record.get("authors") or []:
            fields.extend(_AFFILIATION_CAPTURE_RE.findall(author))

        affiliations: list[str] = []
        for field in fields:
            affiliations.extend(split_institutions(field))
        return affiliations

    @classmethod
    def _uw_affiliations(cls, record: dict[str, Any]) -> list[str]:
        """Return *record*'s structured affiliations that name UW."""
        return uw_affiliations(cls._record_affiliations(record))

    # === RECORD PARSING ===

    def _parse_record(self, record: dict[str, Any], search_term: str = "") -> ArticleItem | None:
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
            extra=self._build_extra(record, search_term),
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

    @classmethod
    def _build_extra(cls, record: dict[str, Any], search_term: str = "") -> dict[str, Any]:
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

        # Keep the evidence that justified collecting this article.  Author
        # affiliations are stripped from the ``authors`` string to match
        # ParsedAuthor conventions, so without this the UW connection would be
        # unverifiable after the crawl.
        if uw := cls._uw_affiliations(record):
            extra["uw_affiliations"] = uw
        if search_term:
            extra["search_term"] = search_term

        return extra
