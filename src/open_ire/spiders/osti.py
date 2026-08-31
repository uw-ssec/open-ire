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
from open_ire.settings import OPEN_IRE_EXCLUDED_INSTITUTIONS, OPEN_IRE_INSTITUTION_NAMES
from open_ire.spiders.search import TermSearchSpider
from open_ire.utils import as_list, parse_date

# Matches "[affiliation text]" or "[affiliation text" (unclosed) in an author name.
_AFFILIATION_RE = re.compile(r"\s*\[.*")
# Captures the affiliation inside "Last, First [affiliation]", tolerating a
# missing closing bracket.
_AFFILIATION_CAPTURE_RE = re.compile(r"\[([^\]]*)\]?")
# Matches "(ORCID:digits)" at the end of an author name.
_ORCID_RE = re.compile(r"\s*\(ORCID:\d+\)\s*$")
# OSTI packs several institutions into one field, separated by semicolons.
_INSTITUTION_SEPARATOR_RE = re.compile(r"\s*;\s*")


class OstiSpider(TermSearchSpider):
    """Collect DOE-funded research articles from the OSTI.GOV API.

    For each search term, queries ``/api/v1/records`` with
    ``has_fulltext=true`` and paginates through all results, yielding an
    :class:`ArticleItem` per record affiliated with our institution.

    OSTI's ``q`` parameter also searches the indexed full text of the PDF, so
    a search hit is not by itself evidence of an affiliation. Records are
    therefore filtered on their own affiliation metadata; see
    :meth:`_affiliation_evidence`.
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
            evidence = self._affiliation_evidence(record)
            if not any(evidence.values()):
                unaffiliated += 1
                self.logger.debug(
                    "Dropping OSTI record %s (%r): matched %r without an affiliation",
                    record.get("osti_id", "<unknown>"),
                    (record.get("title") or "")[:80],
                    search_term,
                )
                continue

            item = self._parse_record(record, search_term, evidence)
            if item is None:
                unusable += 1
                continue
            yield item

        if unaffiliated:
            self.logger.debug(
                "Dropped %d of %d record(s) on page %d for %r: no affiliation",
                unaffiliated,
                len(records),
                current_page,
                search_term,
            )
        if unusable:
            self.logger.debug(
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

    #: Fields naming the institutions behind the work itself. Deliberately
    #: excludes ``title``, ``description`` and ``subjects``: a report *about*
    #: an institution's facility is not that institution's work. OSTI 2311080,
    #: "Evaluation of the Radioactive Material Release in the Harborview
    #: Research Building", was authored at Brookhaven and names UW only in its
    #: title and abstract.
    WORK_AFFILIATION_FIELDS = ("research_orgs", "sponsor_orgs", "contributing_org", "assignee")

    @staticmethod
    def _normalize(value: str) -> str:
        """Collapse whitespace and case-fold *value* for matching."""
        return re.sub(r"\s+", " ", value or "").casefold()

    @classmethod
    def _is_our_institution(cls, institution_string: str) -> bool:
        """Return ``True`` if *institution_string* names our institution.

        *institution_string* must be a single institution, not a whole title
        or abstract: the exclusions below have to apply to the same name they
        disqualify, or one institution's city would rule out another's match.
        """
        name = cls._normalize(institution_string)
        return any(n in name for n in OPEN_IRE_INSTITUTION_NAMES) and not any(
            x in name for x in OPEN_IRE_EXCLUDED_INSTITUTIONS
        )

    @classmethod
    def _institutions(cls, values: list[str]) -> list[str]:
        """Split OSTI's packed affiliation *values* into single institutions."""
        institutions: list[str] = []
        for value in values:
            institutions.extend(
                part.strip()
                for part in _INSTITUTION_SEPARATOR_RE.split(value or "")
                if part.strip()
            )
        return institutions

    @classmethod
    def _work_institutions(cls, record: dict[str, Any]) -> list[str]:
        """Return the institutions credited with the work in *record*."""
        values: list[str] = []
        for field in cls.WORK_AFFILIATION_FIELDS:
            values.extend(as_list(record.get(field)))
        return cls._institutions(values)

    @classmethod
    def _author_institutions(cls, record: dict[str, Any]) -> list[str]:
        """Return the institutions bracketed into *record*'s author entries.

        For a large minority of OSTI records this is the only place an
        affiliation is recorded, and OSTI's ``author:`` index does not cover
        it, so it cannot be reached by a field-scoped query.
        """
        values: list[str] = []
        for author in as_list(record.get("authors")):
            values.extend(_AFFILIATION_CAPTURE_RE.findall(author))
        return cls._institutions(values)

    @classmethod
    def _affiliation_evidence(cls, record: dict[str, Any]) -> dict[str, list[str]]:
        """Return *record*'s affiliations with our institution, by kind.

        OSTI records an affiliation against the work and against individual
        authors, and either alone is enough to collect the article. Both are
        returned so the distinction survives into the stored item.
        """
        return {
            "work": cls._our_institutions(cls._work_institutions(record)),
            "author": cls._our_institutions(cls._author_institutions(record)),
        }

    @classmethod
    def _our_institutions(cls, institutions: list[str]) -> list[str]:
        """Return the entries of *institutions* that are ours, de-duplicated.

        The same institution is usually repeated once per author, so the list
        is de-duplicated while keeping the order it was found in.
        """
        ours = [i for i in institutions if cls._is_our_institution(i)]
        return list(dict.fromkeys(ours))

    # === RECORD PARSING ===

    def _parse_record(
        self,
        record: dict[str, Any],
        search_term: str = "",
        evidence: dict[str, list[str]] | None = None,
    ) -> ArticleItem | None:
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
            extra=self._build_extra(record, search_term, evidence),
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
        """Extract and normalize the DOI, stripping the URL prefix."""
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
    def _build_extra(
        cls,
        record: dict[str, Any],
        search_term: str = "",
        evidence: dict[str, list[str]] | None = None,
    ) -> dict[str, Any]:
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

        for key in ("subjects", "sponsor_orgs", "research_orgs", "contributing_org"):
            if value := record.get(key):
                extra[key] = value

        # Record why this article was collected. `authors` stores names
        # without their affiliations, and the `authoraffiliation` table is not
        # populated yet, so this is currently the only place the affiliation
        # survives the crawl.
        if evidence is None:
            evidence = cls._affiliation_evidence(record)
        if any(evidence.values()):
            extra["affiliation_evidence"] = evidence
        if search_term:
            extra["search_term"] = search_term

        return extra
