import csv
import json
from collections.abc import Generator
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urlencode

from scrapy.http import Request, Response

from open_ire.author import ParsedAuthor
from open_ire.enums import ArticleType
from open_ire.errors import AmbiguousAuthorError
from open_ire.items import ArticleItem, AuthorItem
from open_ire.settings import (
    OPEN_IRE_CONTACT_EMAIL,
    OPENALEX_AMBIGUOUS_AUTHORS_FILE,
    OPENALEX_INSTITUTION_ID,
)
from open_ire.spiders.search import AuthorSearchSpider
from open_ire.utils import parse_date


class OpenAlexSpider(AuthorSearchSpider):
    """
    OpenAlex API spider for collecting academic publications by author.
    """

    name = "openalex"
    base_url = "https://api.openalex.org"
    page_size = 25

    # OpenAlex publication type mappings
    # See https://docs.openalex.org/api-entities/works/work-object#type
    TYPE_MAP: ClassVar[dict[str, ArticleType]] = {
        "article": ArticleType.SCHOLARLY_ARTICLE,
        "preprint": ArticleType.SCHOLARLY_ARTICLE,
        "proceedings-article": ArticleType.SCHOLARLY_ARTICLE,
        "posted-content": ArticleType.SCHOLARLY_ARTICLE,
        "review": ArticleType.SCHOLARLY_ARTICLE,
        "book": ArticleType.OTHER,
        "book-chapter": ArticleType.OTHER,
        "editorial": ArticleType.OTHER,
        "erratum": ArticleType.OTHER,
        "letter": ArticleType.OTHER,
        "libguides": ArticleType.OTHER,
        "paratext": ArticleType.OTHER,
        "supplementary-materials": ArticleType.OTHER,
    }

    # === SUPERCLASS OVERRIDES ===

    def __init__(
        self,
        start_date: str = "2018-01-01",
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)

        self.start_date = start_date
        self.institution_id = OPENALEX_INSTITUTION_ID
        self.request_headers: dict[str, str] = {"User-Agent": f"mailto:{OPEN_IRE_CONTACT_EMAIL}"}
        self.ambiguous_authors_file = Path(OPENALEX_AMBIGUOUS_AUTHORS_FILE)
        self._ambiguous_authors: list[dict[str, Any]] = []

    def author_name_for_query(self, record: ParsedAuthor) -> str:
        return " ".join(
            part for part in [record.first_name, record.middle_names, record.last_name] if part
        )

    def build_search_request(self, record: ParsedAuthor) -> Request:
        """Build the initial search request for a given author record."""
        term = self.author_name_for_query(record)
        matched_author = self.canonical_author_name(record)
        params = {
            "search": term,
            "filter": f"affiliations.institution.id:{self.institution_id}",
            "per_page": str(self.page_size),
        }
        url = f"{self.base_url}/authors?{urlencode(params)}"

        self.logger.info("Searching for author '%s'", term)
        self.logger.debug("Author search URL: %s", url)

        return Request(
            url,
            headers=self.request_headers,
            callback=self._search_for_authors,
            meta={"matched_author": matched_author},
        )

    def closed(self, _reason: str | None = None) -> None:
        self._write_ambiguous_authors_file()

    # === HIGH-LEVEL WORKFLOW METHODS ===

    def _search_for_authors(
        self, response: Response
    ) -> Generator[Request | AuthorItem, None, None]:
        """Parse author search results and generate publication requests."""
        matched_author = response.meta["matched_author"]
        data = json.loads(response.text or "{}")
        authors = data.get("results", [])

        if not authors:
            self.logger.info("No authors found matching '%s'", matched_author)
            return

        self.logger.info("Found %s authors matching '%s':", len(authors), matched_author)
        for i, author in enumerate(authors):
            author_id = author.get("id")
            if not author_id:
                continue

            # TODO: OpenAlex returns a relevance score; we could use it for early filtering.
            self.logger.info(
                "%s) '%s' (ID: %s, ORCID: %s, relevance: %s)",
                f"{i + 1:2d}",
                author.get("display_name"),
                self._bare_openalex_id(author_id),
                author.get("orcid"),
                author.get("relevance_score"),
            )

        if len(authors) > 1:
            try:
                authors = self._disambiguate_authors(authors, matched_author)
            except AmbiguousAuthorError as e:
                self.logger.warning("%s", e)
                self._add_to_ambiguous_authors(matched_author, authors, e.reason)
                return

        # Yield author identifiers for storage
        author_data = authors[0]
        yield self._build_author_item(matched_author, author_data)

        yield from self._request_author_publications(
            author_data.get("id", "unknown"), matched_author
        )

    def _request_author_publications(
        self, author_id: str, matched_author: str, cursor: str = "*"
    ) -> Generator[Request, None, None]:
        """Generate a request for an author's publications with pagination support."""
        params = {
            "filter": f"author.id:{author_id},from_publication_date:{self.start_date}",
            "per_page": str(self.page_size),
            "cursor": cursor,
            "sort": "publication_date:desc",
        }
        url = f"{self.base_url}/works?{urlencode(params)}"

        self.logger.info(
            "Requesting %spublications for %s (ID: %s)",
            "" if cursor == "*" else "next page of ",
            matched_author,
            self._bare_openalex_id(author_id),
        )
        self.logger.debug("Publication request URL: %s", url)

        yield Request(
            url,
            headers=self.request_headers,
            callback=self._parse_publications,
            meta={"matched_author": matched_author, "cursor": cursor},
            cb_kwargs={"author_id": author_id},
        )

    def _build_author_item(self, matched_author: str, author_data: dict[str, Any]) -> AuthorItem:
        """Build an AuthorItem from our data and OpenAlex author data."""
        identifiers = []

        if openalex_id := author_data.get("id"):
            identifiers.append(
                {
                    "authority": "openalex",
                    "identifier": self._bare_openalex_id(openalex_id),
                }
            )

        if orcid_url := author_data.get("orcid"):
            identifiers.append({"authority": "orcid", "identifier": self._bare_orcid(orcid_url)})

        # OpenAlex sometimes provides "parsed_longest_name", but that can
        # introduce surprises, so rely on "our" name.
        parsed_name = ParsedAuthor(matched_author)

        return AuthorItem(
            full_name=parsed_name.full_name,
            first_name=parsed_name.first_name,
            middle_names=parsed_name.middle_names,
            last_name=parsed_name.last_name,
            identifiers=identifiers,
        )

    def _parse_publications(
        self, response: Response, author_id: str
    ) -> Generator[Request | ArticleItem, None, None]:
        """Parse publication results and yield ArticleItems, handling pagination."""
        matched_author = response.meta["matched_author"]
        is_first_page = response.meta["cursor"] == "*"

        data = json.loads(response.text or "{}")
        results = data.get("results", [])
        meta = data.get("meta", {})

        # Log total publications only on first page
        if is_first_page:
            total_count = meta.get("count", 0)

            if total_count == 0:
                self.logger.info(
                    "No publications found for %s (ID: %s)",
                    matched_author,
                    self._bare_openalex_id(author_id),
                )
            else:
                self.logger.info(
                    "Found %s publications for %s (ID: %s):",
                    total_count,
                    matched_author,
                    self._bare_openalex_id(author_id),
                )

        for _i, publication in enumerate(results):
            if not isinstance(publication, dict):
                continue

            if item := self._build_article_item(publication, matched_author):
                self.logger.info(
                    "%s: '%s' (%s)",
                    matched_author,
                    item.title[:50],
                    item.publication_date,
                )
                yield item

        if next_cursor := meta.get("next_cursor"):
            yield from self._request_author_publications(
                author_id, matched_author, cursor=next_cursor
            )

    def _build_article_item(
        self, publication: dict[str, Any], matched_author: str
    ) -> ArticleItem | None:
        """Build an ArticleItem from OpenAlex publication data."""
        external_id = publication.get("id")
        if not external_id:
            return None

        title = publication.get("title")
        if not title:
            self.logger.warning("Skipping publication without title (ID: %s)", external_id)
            return None

        authors = self._extract_authors(publication)
        oa_status = publication.get("open_access", {}).get("oa_status")
        is_oa = publication.get("open_access", {}).get("is_oa")

        raw_type = publication.get("type")
        url = (
            publication.get("doi")
            or publication.get("primary_location", {}).get("landing_page_url")
            or external_id
        )
        return ArticleItem(
            authors=ParsedAuthor.encode_author_string(authors),
            doi=publication.get("doi"),
            extra={
                "is_open_access": is_oa,
                "journal_name": self._extract_journal_name(publication),
                "oa_status": oa_status,
                "matched_author": matched_author,
                "openalex": {
                    "type": raw_type,
                },
            },
            publication_date=parse_date(publication.get("publication_date")),
            reference=str(external_id),
            repository=self.name,
            title=title,
            type=self._normalize_type(raw_type),
            url=url,
        )

    # === HELPER METHODS ===

    def _disambiguate_authors(
        self, authors: list[dict[str, Any]], matched_author: str
    ) -> list[dict[str, Any]]:
        """Attempt to disambiguate multiple author matches by recent institutional affiliation.

        Returns a single-element list if disambiguation succeeds.
        Raises AmbiguousAuthorError if disambiguation fails.
        """
        start_year = int(self.start_date.split("-")[0])
        recently_affiliated = [a for a in authors if self._has_recent_affiliation(a, start_year)]

        if not recently_affiliated or len(recently_affiliated) > 1:
            no_or_multiple = "no" if not recently_affiliated else "multiple"
            raise AmbiguousAuthorError(
                matched_author,
                len(authors),
                f"{no_or_multiple} authors with recent institutional affiliation (>={start_year})",
            )

        self.logger.info(
            "Disambiguated '%s' to '%s' (ID: %s) based on recent institutional affiliation",
            matched_author,
            recently_affiliated[0].get("display_name"),
            self._bare_openalex_id(recently_affiliated[0].get("id", "unknown")),
        )
        return recently_affiliated

    def _has_recent_affiliation(self, author: dict[str, Any], start_year: int) -> bool:
        """Check if the author has an institutional affiliation since start_year."""
        for affiliation in author.get("affiliations", []):
            if not isinstance(affiliation, dict):
                continue

            institution = affiliation.get("institution", {})
            if not isinstance(institution, dict):
                continue

            institution_id = institution.get("id", "")
            if not isinstance(institution_id, str):
                continue

            if institution_id.lower().endswith(self.institution_id.lower()):
                years = affiliation.get("years", [])
                if any(isinstance(year, int) and year >= start_year for year in years):
                    return True

        return False

    def _add_to_ambiguous_authors(
        self,
        matched_author: str,
        authors: list[dict[str, Any]],
        reason: str,
    ) -> None:
        """Store one structured ambiguous-author record for the matched author."""
        self._ambiguous_authors.append(
            {
                "matched_author": matched_author,
                "reason": reason,
                "start_year": int(self.start_date.split("-")[0]),
                "candidates": authors,
            }
        )

    def _write_ambiguous_authors_file(self) -> None:
        """Write a CSV file of ambiguous author records to disk."""
        if not self._ambiguous_authors:
            return

        rows: list[dict[str, str]] = []
        for ambiguous_author in self._ambiguous_authors:
            matched_author = str(ambiguous_author.get("matched_author") or "")
            reason = str(ambiguous_author.get("reason") or "")
            start_year = int(ambiguous_author.get("start_year") or 0)

            raw_candidates = ambiguous_author.get("candidates") or []
            candidates = [c for c in raw_candidates if isinstance(c, dict)]
            candidate_count = len(candidates)

            rows.extend(
                self._build_ambiguous_authors_file_row(
                    matched_author=matched_author,
                    author=author,
                    rank=rank,
                    candidate_count=candidate_count,
                    reason=reason,
                    start_year=start_year,
                )
                for rank, author in enumerate(candidates, start=1)
            )

        if not rows:
            self._ambiguous_authors.clear()
            return

        fieldnames = list(rows[0].keys())
        self.ambiguous_authors_file.parent.mkdir(parents=True, exist_ok=True)
        file_exists = self.ambiguous_authors_file.exists()

        with self.ambiguous_authors_file.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames)
            if not file_exists:
                writer.writeheader()
            writer.writerows(rows)

        self.logger.warning(
            "Wrote %s ambiguous OpenAlex author row(s) to %s",
            len(rows),
            self.ambiguous_authors_file,
        )
        self._ambiguous_authors.clear()

    def _build_ambiguous_authors_file_row(
        self,
        matched_author: str,
        author: dict[str, Any],
        rank: int,
        candidate_count: int,
        reason: str,
        start_year: int,
    ) -> dict[str, str]:
        """Build a single CSV row for manual disambiguation review."""
        openalex_id = str(author.get("id") or "")
        years, institutions = self._extract_affiliation_details(author)
        last_known_names = self._extract_last_known_affiliations(author)

        return {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "matched_author": matched_author,
            "candidate_rank": str(rank),
            "candidate_count": str(candidate_count),
            "ambiguity_reason": reason,
            "start_year": str(start_year),
            "candidate_openalex_id": self._bare_openalex_id(openalex_id),
            "candidate_openalex_url": openalex_id,
            "candidate_display_name": str(author.get("display_name") or ""),
            "candidate_orcid": self._bare_openalex_id(str(author.get("orcid") or "")),
            "candidate_relevance_score": str(author.get("relevance_score") or ""),
            "candidate_works_count": str(author.get("works_count") or ""),
            "candidate_cited_by_count": str(author.get("cited_by_count") or ""),
            "institution_affiliation_years": ";".join(str(y) for y in sorted(set(years))),
            "last_known_affiliations": ";".join(sorted(set(last_known_names))),
        }

    def _extract_affiliation_details(self, author: dict[str, Any]) -> tuple[list[int], list[str]]:
        """Extract institutional affiliation years and institution names from an author candidate."""
        years: list[int] = []
        institutions: list[str] = []

        affiliations = author.get("affiliations") or []
        for affiliation in affiliations:
            if not isinstance(affiliation, dict):
                continue

            institution = affiliation.get("institution") or {}
            if not isinstance(institution, dict):
                continue

            institution_id = institution.get("id", "")
            if not (
                isinstance(institution_id, str)
                and institution_id.lower().endswith(self.institution_id.lower())
            ):
                continue

            display_name = institution.get("display_name")
            if isinstance(display_name, str) and display_name:
                institutions.append(display_name)

            years = affiliation.get("years") or []
            years.extend(year for year in years if isinstance(year, int))

        return years, institutions

    @staticmethod
    def _extract_last_known_affiliations(author: dict[str, Any]) -> list[str]:
        """Extract last-known institution display names from an author candidate.

        These are one or more affiliations that the author listed in their most
        recent OpenAlex-indexed publication.
        """
        institutions = author.get("last_known_institutions") or []
        institution_names: list[str] = []
        for institution in institutions:
            if not isinstance(institution, dict):
                continue

            display_name = institution.get("display_name")
            if isinstance(display_name, str) and display_name:
                institution_names.append(display_name)

        return institution_names

    @staticmethod
    def _extract_authors(publication: dict[str, Any]) -> list[ParsedAuthor]:
        """Extract author names from publication authorship data."""
        authors: list[ParsedAuthor] = []
        authorships = publication.get("authorships", [])
        for authorship in authorships:
            if not isinstance(authorship, dict):
                continue

            display_name = authorship.get("author", {}).get("display_name")
            if display_name and isinstance(display_name, str):
                authors.append(ParsedAuthor(display_name))

        return authors

    @staticmethod
    def _extract_journal_name(publication: dict[str, Any]) -> str | None:
        """Extract journal name from publication location data."""
        primary_location = publication.get("primary_location") or {}
        if isinstance(primary_location, dict):
            source = primary_location.get("source") or {}
            if isinstance(source, dict) and source.get("display_name"):
                return str(source["display_name"])

        for location in publication.get("locations", []) or []:
            if not isinstance(location, dict):
                continue

            source = location.get("source") or {}
            display_name = source.get("display_name")
            if display_name and isinstance(display_name, str):
                return str(display_name)

        return None

    @staticmethod
    def _bare_openalex_id(openalex_id: str) -> str:
        """Extract just the ID part from a full OpenAlex identifier."""
        if not openalex_id:
            return ""
        # https://openalex.org/A5077779935 => A5077779935
        return openalex_id.split("/")[-1]

    @staticmethod
    def _bare_orcid(orcid_url: str) -> str:
        """Extract just the ID part from a full ORCID URL."""
        if not orcid_url:
            return ""
        # https://orcid.org/0000-0002-4664-9847 => 0000-0002-4664-9847
        return orcid_url.split("/")[-1]

    @staticmethod
    def _normalize_type(raw_type: str | None) -> ArticleType | None:
        """Normalize OpenAlex publication type to ArticleType."""
        if raw_type is None:
            return None
        return OpenAlexSpider.TYPE_MAP.get(raw_type.lower())
