"""Spider to collect open-access articles from PubMed Central (PMC).

This spider queries the NCBI E-utilities API for PMC records matching
configurable search terms and yields one :class:`~open_ire.items.ArticleItem`
per open-access record found.

The crawl uses a three-step flow:

1. ``esearch.fcgi`` returns the list of PMC UIDs matching a query, paginated
   via ``retstart``/``retmax``.
2. ``esummary.fcgi`` returns document summaries (title, authors, journal, IDs,
   publication date) for a batch of UIDs.
3. The PMC Open Access Subset on AWS (the ``pmc-oa-opendata`` S3 bucket) is
   queried to locate the full-text PDF for each record.

Searches are restricted to the open-access subset (``open access[filter]``) so
that a record is available in the OA Subset bucket. Two NCBI download routes are
intentionally *not* used: the web ``/articles/PMC.../pdf/`` endpoint is served
behind a bot-verification interstitial (returns HTML, not the PDF), and the
legacy ``oa.fcgi`` service only hands back ``ftp://`` links that no longer
resolve. The AWS Open Data bucket serves the same OA content over plain HTTPS;
each article lives under ``PMC<id>.<version>/`` with the main PDF named
``PMC<id>.<version>.pdf``. The version is discovered with an S3 list request.

API documentation:

* E-utilities: https://www.ncbi.nlm.nih.gov/books/NBK25501/
* PMC OA Subset on AWS: https://www.ncbi.nlm.nih.gov/pmc/tools/openftlist/

Usage::

    pixi run scrapy crawl pmc
    pixi run scrapy crawl pmc -a terms="university of washington"

An optional ``NCBI_API_KEY`` environment variable raises the per-IP rate limit
from 3 to 10 requests/second; it is included automatically when present.
"""

import json
import os
import re
from collections.abc import Generator
from datetime import date, timedelta
from typing import Any, cast
from urllib.parse import urlencode

from scrapy.http import Request, Response, TextResponse

from open_ire.author import ParsedAuthor
from open_ire.items import ArticleItem
from open_ire.settings import OPEN_IRE_CONTACT_EMAIL
from open_ire.spiders.search import TermSearchSpider
from open_ire.utils import parse_date

type DateRange = tuple[date, date]

# esearch refuses to page past this many results for one query.
_MAX_RESULTS = 9999
# Publication dates searched when a query must be split to stay under _MAX_RESULTS.
_FULL_DATE_RANGE: DateRange = (date(1600, 1, 1), date(2999, 12, 31))
# Affiliation terms that also match other institutions, and what to exclude.
# "uw.edu" matches University of Warsaw addresses (uw.edu.pl), and
# "university of washington" matches Washington University in St. Louis
# labelled "University of Washington, St. Louis".
_AFFILIATION_EXCLUSIONS = {
    "uw.edu": '"uw.edu.pl"[Affiliation]',
    "university of washington": (
        '("university of washington st louis"[Affiliation] NOT '
        '("university of washington seattle"[Affiliation] OR "uw.edu"[Affiliation] '
        'OR "washington.edu"[Affiliation]))'
    ),
}


class PMCSpider(TermSearchSpider):
    """Collect open-access articles from PubMed Central via NCBI E-utilities.

    For each search term, queries ``esearch`` for matching PMC UIDs and follows
    up with ``esummary`` to build an :class:`ArticleItem` per open-access record.
    """

    name = "pmc"
    eutils_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    oa_bucket_url = "https://pmc-oa-opendata.s3.amazonaws.com"
    article_base_url = "https://pmc.ncbi.nlm.nih.gov/articles"
    db = "pmc"
    tool = "open_ire"
    page_size = 100

    custom_settings = {  # noqa: RUF012
        # NCBI permits 3 requests/second per IP without an API key (10 with one).
        # A 1-second delay with no concurrency keeps us comfortably within the
        # unauthenticated limit across the esearch -> esummary chain.
        "DOWNLOAD_DELAY": 1,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        # E-utilities is the sanctioned programmatic interface; NCBI's robots.txt
        # targets crawlers of the HTML site, not the API or the OA Subset bucket.
        "ROBOTSTXT_OBEY": False,
        # NCBI answers bursts with 429 and, under load, transient 5xx errors.
        # A lost esearch/esummary page drops up to 100 articles, so back off and retry.
        "DOWNLOADER_MIDDLEWARES": {
            "open_ire.middlewares.BackoffRetryMiddleware": 560,
        },
        "OPEN_IRE_BACKOFF_RETRY_STATUSES": [429, 500, 502, 503, 504],
        "OPEN_IRE_BACKOFF_RETRY_TIMES": 10,
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.api_key = os.getenv("NCBI_API_KEY") or None

    # === REQUEST BUILDING ===

    def build_search_request(self, term: str) -> Request:
        """Build the first-page esearch request for *term*."""
        self.logger.info("Searching PMC for %r (page_size=%d)", term, self.page_size)
        return self._build_esearch_request(term, retstart=0)

    def _eutils_params(self, extra: dict[str, str]) -> dict[str, str]:
        """Return the common E-utilities query parameters merged with *extra*.

        Includes the NCBI-recommended ``tool`` and ``email`` identifiers and the
        optional ``api_key`` when configured.
        """
        params = {
            "db": self.db,
            "retmode": "json",
            "tool": self.tool,
            "email": OPEN_IRE_CONTACT_EMAIL,
        }
        if self.api_key:
            params["api_key"] = self.api_key
        params.update(extra)
        return params

    def _build_esearch_request(
        self, term: str, retstart: int, dates: DateRange | None = None
    ) -> Request:
        """Build an esearch request for *term* starting at *retstart*.

        *dates* limits the search to an inclusive publication-date range.
        """
        extra = {
            "term": self._build_query(term),
            "retmax": str(self.page_size),
            "retstart": str(retstart),
        }
        if dates:
            extra |= {
                "datetype": "pdat",
                "mindate": dates[0].strftime("%Y/%m/%d"),
                "maxdate": dates[1].strftime("%Y/%m/%d"),
            }
        url = f"{self.eutils_url}/esearch.fcgi?{urlencode(self._eutils_params(extra))}"
        return Request(
            url,
            callback=self.parse,
            headers={"Accept": "application/json"},
            meta={"search_term": term, "retstart": retstart, "dates": dates},
        )

    def _build_esummary_request(self, term: str, uids: list[str]) -> Request:
        """Build an esummary request for a batch of *uids*."""
        params = self._eutils_params({"id": ",".join(uids)})
        url = f"{self.eutils_url}/esummary.fcgi?{urlencode(params)}"
        return Request(
            url,
            callback=self.parse_summary,
            headers={"Accept": "application/json"},
            meta={"search_term": term},
        )

    @staticmethod
    def _build_query(term: str) -> str:
        """Build the PMC query string for *term*.

        Restricts results to UW-affiliated records in the open-access subset so
        a downloadable full-text PDF is available for every match, excluding
        other institutions that the term also matches.
        """
        query = f'"{term}"[Affiliation]'
        if exclude := _AFFILIATION_EXCLUSIONS.get(term.casefold()):
            query = f"({query} NOT {exclude})"
        return f"{query} AND open access[filter]"

    # === RESPONSE PARSING ===

    def parse(self, response: Response, **kwargs: Any) -> Generator[Request]:  # noqa: ARG002
        """Parse an esearch page, fetch summaries, and follow pagination."""
        search_term: str = response.meta["search_term"]
        retstart: int = response.meta["retstart"]
        dates: DateRange | None = response.meta.get("dates")

        try:
            payload = json.loads(response.text or "{}")
        except json.JSONDecodeError:
            self.logger.warning("PMC returned invalid JSON for esearch %r", search_term)
            return

        result = payload.get("esearchresult", {})
        if error := result.get("ERROR"):
            self.logger.warning("PMC esearch error for %r: %s", search_term, error)
        uids = [str(uid) for uid in result.get("idlist", []) if uid]
        try:
            count = int(result.get("count", 0))
        except (TypeError, ValueError):
            count = 0

        self.logger.info(
            "PMC esearch returned %d UID(s) for %r (dates=%s, retstart=%d, total=%d)",
            len(uids),
            search_term,
            dates,
            retstart,
            count,
        )

        if count > _MAX_RESULTS and retstart == 0:
            if halves := self._split_dates(dates or _FULL_DATE_RANGE):
                for half in halves:
                    yield self._build_esearch_request(search_term, 0, half)
                return
            self.logger.warning(
                "PMC search %r (dates=%s) has %d results; only the first %d can be fetched",
                search_term,
                dates,
                count,
                _MAX_RESULTS,
            )

        if not uids:
            return

        yield self._build_esummary_request(search_term, uids)

        next_retstart = retstart + self.page_size
        if next_retstart < min(count, _MAX_RESULTS):
            self.logger.debug(
                "Requesting next PMC page (retstart=%d) for %r", next_retstart, search_term
            )
            yield self._build_esearch_request(search_term, next_retstart, dates)

    @staticmethod
    def _split_dates(dates: DateRange) -> tuple[DateRange, DateRange] | None:
        """Split *dates* into two non-overlapping halves, or ``None`` for a single day."""
        start, end = dates
        if start >= end:
            return None
        mid = start + (end - start) // 2
        return (start, mid), (mid + timedelta(days=1), end)

    def parse_summary(self, response: Response, **kwargs: Any) -> Generator[Request]:  # noqa: ARG002
        """Parse an esummary response and request the OA link for each record."""
        search_term: str = response.meta["search_term"]

        try:
            payload = json.loads(response.text or "{}")
        except json.JSONDecodeError:
            self.logger.warning("PMC returned invalid JSON for esummary %r", search_term)
            return

        result = payload.get("result", {})
        uids: list[str] = [str(uid) for uid in result.get("uids", []) if uid]

        yielded = 0
        for uid in uids:
            record = result.get(uid)
            if not isinstance(record, dict):
                continue
            item = self._parse_record(record)
            if item is not None:
                yielded += 1
                yield self._build_oa_lookup_request(item)

        if yielded < len(uids):
            self.logger.info(
                "Skipped %d PMC record(s) for %r (missing title or PMCID)",
                len(uids) - yielded,
                search_term,
            )

    def _build_oa_lookup_request(self, item: ArticleItem) -> Request:
        """Build an S3 list request to locate the full-text PDF for *item*.

        The OA Subset bucket stores each article under ``PMC<id>.<version>/``;
        listing by the bare PMCID prefix reveals the available object keys
        (and the version, which is not exposed by esummary).
        """
        params = {"list-type": "2", "prefix": item.reference}
        url = f"{self.oa_bucket_url}/?{urlencode(params)}"
        return Request(
            url,
            callback=self.parse_oa_listing,
            errback=self.handle_oa_error,
            cb_kwargs={"item": item},
            dont_filter=True,
        )

    def parse_oa_listing(self, response: Response, item: ArticleItem) -> Generator[ArticleItem]:
        """Attach the OA full-text PDF link to *item* (if any), then yield it.

        The bucket may hold supplementary PDFs and figures alongside the main
        article PDF, so only the ``PMC<id>.<version>/PMC<id>.<version>.pdf`` key
        is selected. When no such key exists the item is yielded without a file.
        """
        selector = cast(TextResponse, response).selector
        selector.remove_namespaces()
        keys = selector.xpath("//Contents/Key/text()").getall()

        pdf_key = self._select_pdf_key(item.reference, keys)
        if pdf_key:
            item.file_urls = [f"{self.oa_bucket_url}/{pdf_key}"]
        else:
            self.logger.debug("No OA Subset PDF found for %s", item.reference)

        yield item

    @staticmethod
    def _select_pdf_key(pmcid: str, keys: list[str]) -> str | None:
        """Return the main-article PDF key for *pmcid* from S3 *keys*.

        Matches ``PMC<id>.<version>/PMC<id>.<version>.pdf`` exactly (the prefix
        query can also return neighbouring IDs, e.g. ``PMC123`` matches
        ``PMC1230``), preferring the highest version when several exist.
        """
        pattern = re.compile(rf"^{re.escape(pmcid)}\.(\d+)/{re.escape(pmcid)}\.\1\.pdf$")
        matches = [(int(m.group(1)), key) for key in keys if (m := pattern.match(key))]
        if not matches:
            return None
        return max(matches)[1]

    def handle_oa_error(self, failure: Any) -> Generator[ArticleItem]:
        """Yield the item without a file link when the OA lookup fails."""
        item: ArticleItem = failure.request.cb_kwargs["item"]
        self.logger.debug("OA lookup failed for %s (%s)", item.reference, failure.value)
        yield item

    # === RECORD PARSING ===

    def _parse_record(self, record: dict[str, Any]) -> ArticleItem | None:
        """Convert a single esummary record dict into an :class:`ArticleItem`."""
        title = (record.get("title") or "").strip()
        pmcid = self._extract_pmcid(record)

        if not title or not pmcid:
            self.logger.debug(
                "Skipping record with missing title or PMCID: %s",
                record.get("uid", "<unknown>"),
            )
            return None

        # ``file_urls`` is populated later from the OA Subset bucket listing (see
        # parse_oa_listing); esummary does not include a downloadable full-text link.
        return ArticleItem(
            authors=self._extract_authors(record),
            doi=self._extract_article_id(record, "doi"),
            extra=self._build_extra(record),
            publication_date=parse_date(
                record.get("sortdate")
                or record.get("epubdate")
                or record.get("pubdate")
                or record.get("printpubdate")
            ),
            reference=pmcid,
            repository=self.name,
            title=title,
            url=f"{self.article_base_url}/{pmcid}/",
        )

    # === FIELD EXTRACTION HELPERS ===

    @staticmethod
    def _extract_article_id(record: dict[str, Any], idtype: str) -> str | None:
        """Return the value of the ``articleids`` entry matching *idtype*."""
        for entry in record.get("articleids") or []:
            if isinstance(entry, dict) and entry.get("idtype") == idtype:
                value = str(entry.get("value") or "").strip()
                if value:
                    return value
        return None

    @classmethod
    def _extract_pmcid(cls, record: dict[str, Any]) -> str | None:
        """Return the PMCID (e.g. ``PMC123456``) for a record.

        Prefers the explicit ``pmcid`` article id and falls back to deriving it
        from the record's numeric UID.
        """
        pmcid = cls._extract_article_id(record, "pmcid")
        if pmcid:
            # esummary may return "PMC123456" or "PMC123456.1"; keep the bare ID.
            return pmcid.split(".", 1)[0].strip()

        uid = str(record.get("uid") or "").strip()
        return f"PMC{uid}" if uid else None

    @staticmethod
    def _format_author_name(raw: str) -> str:
        """Convert an NCBI ``"Surname Initials"`` name to ``"Surname, Initials"``.

        NCBI returns author names as ``"Habell-Pallán M"`` or ``"Smith JA"``.
        Reformatting to ``Last, First`` lets :class:`ParsedAuthor` parse the
        surname and initials correctly.
        """
        raw = raw.strip()
        surname, _, initials = raw.rpartition(" ")
        if surname and initials:
            return f"{surname}, {initials}"
        return raw

    @classmethod
    def _extract_authors(cls, record: dict[str, Any]) -> str | None:
        """Encode the personal-author list from an esummary record.

        Organizational authors (``authtype == "CollectiveName"``) are excluded:
        their multi-word names are not personal names and :class:`ParsedAuthor`
        (backed by ``HumanName``) mangles them into bogus person rows. They are
        preserved separately via :meth:`_extract_collective_authors`. Properly
        modelling consortium authors is tracked in issue #125.
        """
        cleaned: list[ParsedAuthor] = []
        for entry in record.get("authors") or []:
            if not isinstance(entry, dict) or entry.get("authtype") == "CollectiveName":
                continue
            name = (entry.get("name") or "").strip()
            if name:
                cleaned.append(ParsedAuthor(cls._format_author_name(name)))

        return ParsedAuthor.encode_author_string(cleaned) if cleaned else None

    @staticmethod
    def _extract_collective_authors(record: dict[str, Any]) -> list[str]:
        """Return the verbatim names of organizational/consortium authors."""
        return [
            name
            for entry in record.get("authors") or []
            if isinstance(entry, dict)
            and entry.get("authtype") == "CollectiveName"
            and (name := (entry.get("name") or "").strip())
        ]

    @classmethod
    def _build_extra(cls, record: dict[str, Any]) -> dict[str, Any]:
        """Collect supplementary metadata into the ``extra`` dict."""
        extra: dict[str, Any] = {}

        journal_name = (record.get("fulljournalname") or record.get("source") or "").strip()
        if journal_name:
            extra["journal_name"] = journal_name

        for key in ("volume", "issue", "pages"):
            if value := (record.get(key) or "").strip():
                extra[key] = value

        if pmid := cls._extract_article_id(record, "pmid"):
            extra["pmid"] = pmid

        if collective_authors := cls._extract_collective_authors(record):
            extra["collective_authors"] = collective_authors

        return extra
