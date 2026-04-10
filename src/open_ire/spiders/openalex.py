import csv
import json
from collections import namedtuple
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
    OPEN_IRE_OPENALEX_AMBIGUOUS_AUTHORS_FILE,
    OPEN_IRE_OPENALEX_INSTITUTION_ID,
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

    # === SUPERCLASS OVERRIDES ===

    def __init__(
        self,
        start_date: str = "2018-01-01",
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)

        self.start_date = start_date
        self.our_institution_id = OPEN_IRE_OPENALEX_INSTITUTION_ID.strip().upper()
        self.request_headers: dict[str, str] = {"User-Agent": f"mailto:{OPEN_IRE_CONTACT_EMAIL}"}
        self.ambiguous_authors_file = Path(OPEN_IRE_OPENALEX_AMBIGUOUS_AUTHORS_FILE)
        self._ambiguous_authors: list[dict[str, Any]] = []

    def author_name_for_query(self, record: ParsedAuthor) -> str:
        return " ".join(
            part for part in [record.first_name, record.middle_names, record.last_name] if part
        )

    def build_search_request(self, record: ParsedAuthor) -> Request:
        """Build the initial search request for a given author record."""
        term = self.author_name_for_query(record)
        searched_author = self.canonical_author_name(record)
        params = {
            "search": term,
            "filter": f"affiliations.institution.id:{self.our_institution_id}",
            "per_page": str(self.page_size),
        }
        url = f"{self.base_url}/authors?{urlencode(params)}"

        self.logger.info("Searching for author '%s'", term)
        self.logger.debug("Author search URL: %s", url)

        return Request(
            url,
            headers=self.request_headers,
            callback=self._search_for_authors,
            meta={"searched_author": searched_author},
        )

    def closed(self, _reason: str | None = None) -> None:
        self._write_ambiguous_authors_file()

    # === HIGH-LEVEL WORKFLOW METHODS ===

    def _search_for_authors(
        self, response: Response
    ) -> Generator[Request | AuthorItem, None, None]:
        """Parse author search results and generate publication requests."""
        searched_author = response.meta["searched_author"]
        data = json.loads(response.text or "{}")
        authors = data.get("results", [])

        if not authors:
            self.logger.info("No authors found matching '%s'", searched_author)
            return

        self.logger.info("Found %s authors matching '%s':", len(authors), searched_author)
        for i, author in enumerate(authors):
            author_id = self._extract_author_id(author, searched_author)

            self.logger.info(
                "%s) '%s' (ID: %s, ORCID: %s, relevance: %s)",
                f"{i + 1:2d}",
                author.get("display_name"),
                self._id_from_uri(author_id),
                author.get("orcid"),
                author.get("relevance_score"),
            )

        the_author = authors[0] if len(authors) == 1 else None
        if not the_author:
            try:
                the_author = self._disambiguate_authors(authors, searched_author)
            except AmbiguousAuthorError as e:
                self.logger.warning("%s", e)
                self._add_to_ambiguous_authors(searched_author, e.candidates, e.reason)
                return

        yield self._build_author_item(searched_author, the_author)

        author_id = self._extract_author_id(the_author, searched_author)
        yield from self._request_author_publications(author_id, searched_author)

    def _request_author_publications(
        self, author_id: str, searched_author: str, cursor: str = "*"
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
            searched_author,
            self._id_from_uri(author_id),
        )
        self.logger.debug("Publication request URL: %s", url)

        yield Request(
            url,
            headers=self.request_headers,
            callback=self._parse_publications,
            meta={"searched_author": searched_author, "cursor": cursor},
            cb_kwargs={"author_id": author_id},
        )

    def _build_author_item(self, searched_author: str, author_record: dict[str, Any]) -> AuthorItem:
        """Build an AuthorItem from our data and OpenAlex author data."""
        identifiers = []

        if openalex_id := author_record.get("id"):
            identifiers.append(
                {
                    "authority": "openalex",
                    "identifier": self._id_from_uri(openalex_id),
                }
            )

        if orcid_url := author_record.get("orcid"):
            identifiers.append({"authority": "orcid", "identifier": self._id_from_uri(orcid_url)})

        # OpenAlex sometimes provides "parsed_longest_name", but that can
        # introduce surprises, so rely on "our" name.
        return AuthorItem(
            author=ParsedAuthor(searched_author),
            identifiers=identifiers,
        )

    def _parse_publications(
        self, response: Response, author_id: str
    ) -> Generator[Request | ArticleItem, None, None]:
        """Parse publication results and yield ArticleItems, handling pagination."""
        searched_author = response.meta["searched_author"]
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
                    searched_author,
                    self._id_from_uri(author_id),
                )
            else:
                self.logger.info(
                    "Found %s publications for %s (ID: %s):",
                    total_count,
                    searched_author,
                    self._id_from_uri(author_id),
                )

        for _i, publication in enumerate(results):
            if not isinstance(publication, dict):
                continue

            if item := self._build_article_item(publication, searched_author):
                self.logger.info(
                    "%s: '%s' (%s)",
                    searched_author,
                    item.title[:50],
                    item.publication_date,
                )
                yield item

        if next_cursor := meta.get("next_cursor"):
            yield from self._request_author_publications(
                author_id, searched_author, cursor=next_cursor
            )

    def _build_article_item(
        self, publication_record: dict[str, Any], searched_author: str
    ) -> ArticleItem | None:
        """Build an ArticleItem from OpenAlex publication data."""
        external_id = publication_record.get("id")
        if not external_id:
            return None

        title = publication_record.get("title")
        if not title:
            self.logger.warning("Skipping publication without title (ID: %s)", external_id)
            return None

        authors = self._extract_authors(publication_record)
        oa_status = publication_record.get("open_access", {}).get("oa_status")
        is_oa = publication_record.get("open_access", {}).get("is_oa")

        publication_type = publication_record.get("type")
        url = (
            publication_record.get("doi")
            or publication_record.get("primary_location", {}).get("landing_page_url")
            or external_id
        )
        return ArticleItem(
            authors=ParsedAuthor.encode_author_string(authors),
            doi=publication_record.get("doi"),
            extra={
                "is_open_access": is_oa,
                "journal_name": self._extract_journal_name(publication_record),
                "oa_status": oa_status,
                "searched_author": searched_author,
                "openalex": {
                    "type": publication_type,
                },
            },
            publication_date=parse_date(publication_record.get("publication_date")),
            reference=str(external_id),
            repository=self.name,
            title=title,
            type=self._normalize_type(publication_type),
            url=url,
        )

    # === AUTHOR DISAMBIGUATION ===

    Institution = namedtuple("Institution", ["id", "name"])
    Affiliation = namedtuple("Affiliation", ["institution", "years"])

    def _disambiguate_authors(
        self, author_records: list[dict[str, Any]], searched_author: str
    ) -> dict[str, Any]:
        """Attempt to disambiguate multiple author matches by recent institutional affiliation.

        Returns a single-element list if disambiguation succeeds.
        Raises AmbiguousAuthorError if disambiguation fails.
        """
        affiliated_authors = []

        for author_record in author_records:
            affiliations = self._extract_affiliations(author_record)
            institution_years = self._years_at_institution(self.our_institution_id, affiliations)
            if not institution_years:
                continue
            affiliated_authors.append(author_record)

        if not affiliated_authors or len(affiliated_authors) > 1:
            rough_number = "no" if not affiliated_authors else "multiple"
            raise AmbiguousAuthorError(
                author_name=searched_author,
                candidates=affiliated_authors,
                reason=f"{rough_number} authors with institutional affiliation",
            )

        the_author = affiliated_authors[0]
        self.logger.info(
            "Disambiguated '%s' to '%s' (ID: %s) based on recent institutional affiliation",
            searched_author,
            the_author.get("display_name"),
            self._id_from_uri(self._extract_author_id(the_author, searched_author)),
        )
        return the_author

    def _add_to_ambiguous_authors(
        self,
        searched_author: str,
        author_records: list[dict[str, Any]],
        reason: str,
    ) -> None:
        """Store one structured ambiguous-author record for the matched author."""
        self._ambiguous_authors.append(
            {
                "searched_author": searched_author,
                "reason": reason,
                "start_year": int(self.start_date.split("-")[0]),
                "candidates": author_records,
            }
        )

    def _write_ambiguous_authors_file(self) -> None:
        """Write a CSV file of ambiguous author records to disk."""
        if not self._ambiguous_authors:
            return

        rows: list[dict[str, str]] = []
        for ambiguous_author in self._ambiguous_authors:
            searched_author = str(ambiguous_author.get("searched_author") or "")
            reason = str(ambiguous_author.get("reason") or "")

            raw_candidates = ambiguous_author.get("candidates") or []
            candidates = [c for c in raw_candidates if isinstance(c, dict)]
            candidate_count = len(candidates)

            for rank, author in enumerate(candidates, start=1):
                row = self._build_ambiguous_authors_file_row(
                    searched_author=searched_author,
                    author_record=author,
                    rank=rank,
                    candidate_count=candidate_count,
                    ambiguity_reason=reason,
                )
                rows.append(row)

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

        unique_searched_authors = {
            row["searched_author"] for row in rows if row.get("searched_author")
        }
        self.logger.warning(
            "Added %s ambiguous OpenAlex author(s) to %s",
            len(unique_searched_authors),
            self.ambiguous_authors_file,
        )
        self._ambiguous_authors.clear()

    def _build_ambiguous_authors_file_row(
        self,
        searched_author: str,
        author_record: dict[str, Any],
        rank: int,
        candidate_count: int,
        ambiguity_reason: str,
    ) -> dict[str, str]:
        """Build a single CSV row for manual disambiguation review."""
        openalex_id = str(author_record.get("id") or "")
        affiliations = self._extract_affiliations(author_record)
        institution_years = self._years_at_institution(self.our_institution_id, affiliations)
        last_known_institutions = [
            self._extract_institution(lki).name
            for lki in (author_record.get("last_known_institutions", []) or [])
            if lki is not None
        ]

        return {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "searched_author": searched_author,
            "candidate_rank": str(rank),
            "candidate_count": str(candidate_count),
            "ambiguity_reason": ambiguity_reason,
            "openalex_id": openalex_id,
            "display_name": str(author_record.get("display_name", "")),
            "orcid": self._id_from_uri(str(author_record.get("orcid", ""))),
            "relevance_score": str(author_record.get("relevance_score", -1)),
            "works_count": str(author_record.get("works_count", -1)),
            "cited_by_count": str(author_record.get("cited_by_count", -1)),
            "years_affiliated": ",".join([str(y) for y in sorted(institution_years)]),
            "last_known_institutions": ";".join(last_known_institutions),
        }

    @staticmethod
    def _years_at_institution(institution_id: str, affiliations: list[Affiliation]) -> set[int]:
        """Extract the years of affiliation with an institution."""
        years: set[int] = set()
        for affiliation in affiliations:
            if not OpenAlexSpider._same_institution(affiliation.institution.id, institution_id):
                continue
            years.update(affiliation.years)
        return years

    @staticmethod
    def _extract_affiliations(author_record: dict[str, Any]) -> list[Affiliation]:
        """Extract institutional affiliation details from an author record."""
        affiliations: list[OpenAlexSpider.Affiliation] = []

        affiliation_records = author_record.get("affiliations") or []
        for record in affiliation_records:
            if not isinstance(record, dict):
                continue

            institution_record = record.get("institution") or {}
            if not isinstance(institution_record, dict):
                continue

            affiliations.append(
                OpenAlexSpider.Affiliation(
                    OpenAlexSpider._extract_institution(institution_record), record.get("years", [])
                )
            )

        return affiliations

    @staticmethod
    def _extract_institution(institution_record: dict[str, Any]) -> Institution:
        """Extract Institution from the institution record."""
        institution_id = institution_record.get("id", "")
        institution_name = institution_record.get("display_name", "")
        return OpenAlexSpider.Institution(institution_id, institution_name)

    # === HELPER METHODS ===

    @staticmethod
    def _extract_authors(publication_record: dict[str, Any]) -> list[ParsedAuthor]:
        """Extract author names from publication authorship data."""
        authors: list[ParsedAuthor] = []
        authorships = publication_record.get("authorships", [])
        for authorship in authorships:
            if not isinstance(authorship, dict):
                continue

            display_name = authorship.get("author", {}).get("display_name")
            if display_name and isinstance(display_name, str):
                authors.append(ParsedAuthor(display_name))

        return authors

    @staticmethod
    def _extract_author_id(author_record: dict[str, Any], searched_author: str) -> str:
        """Ensure that the author record has an ID and return it."""
        author_id = author_record.get("id")
        if not author_id:
            msg = f"Author match for '{searched_author}' has no ID: {author_record}"
            raise ValueError(msg)
        assert isinstance(author_id, str)
        return author_id

    @staticmethod
    def _extract_journal_name(publication_record: dict[str, Any]) -> str | None:
        """Extract the journal name from publication location data."""
        primary_location = publication_record.get("primary_location") or {}
        if isinstance(primary_location, dict):
            source = primary_location.get("source") or {}
            if isinstance(source, dict) and source.get("display_name"):
                return str(source["display_name"])

        for location in publication_record.get("locations", []) or []:
            if not isinstance(location, dict):
                continue

            source = location.get("source") or {}
            display_name = source.get("display_name")
            if display_name and isinstance(display_name, str):
                return str(display_name)

        return None

    @staticmethod
    def _id_from_uri(uri: str) -> str:
        """Extract the ID part from a full URI (e.g., OpenAlex or ORCID).

        Examples:
            https://openalex.org/A5077779935 => A5077779935
            https://orcid.org/0000-0002-4664-9847 => 0000-0002-4664-9847

        Returns:
             Upcased ID string, or input string if the string is not a URI."""
        if not uri.startswith(("https://", "http://")):
            return uri
        return uri.split("/")[-1].upper()

    @staticmethod
    def _same_institution(id_a: str | None, id_b: str | None) -> bool:
        """Check if two institution IDs are the same.

        Returns:
            True if the institution IDs are the same, False otherwise. If either ID is None,
            returns False.
        """
        if not id_a or not id_b:
            return False
        return (
            OpenAlexSpider._id_from_uri(id_a).casefold()
            == OpenAlexSpider._id_from_uri(id_b).casefold()
        )

    # OpenAlex publication type mappings
    # See https://docs.openalex.org/api-entities/works/work-object#type
    TYPE_MAP: ClassVar[dict[str, ArticleType]] = {
        "article": ArticleType.SCHOLARLY_ARTICLE,
        "book": ArticleType.OTHER,
        "book-chapter": ArticleType.OTHER,
        "book-section": ArticleType.OTHER,
        "database": ArticleType.OTHER,
        "dataset": ArticleType.OTHER,
        "dissertation": ArticleType.OTHER,
        "editorial": ArticleType.OTHER,
        "erratum": ArticleType.OTHER,
        "grant": ArticleType.OTHER,
        "letter": ArticleType.OTHER,
        "libguides": ArticleType.OTHER,
        "other": ArticleType.OTHER,
        "paratext": ArticleType.OTHER,
        "peer-review": ArticleType.OTHER,
        "posted-content": ArticleType.SCHOLARLY_ARTICLE,
        "preprint": ArticleType.SCHOLARLY_ARTICLE,
        "proceedings-article": ArticleType.SCHOLARLY_ARTICLE,
        "reference-entry": ArticleType.OTHER,
        "report": ArticleType.OTHER,
        "report-component": ArticleType.OTHER,
        "retraction": ArticleType.OTHER,
        "review": ArticleType.SCHOLARLY_ARTICLE,
        "software": ArticleType.OTHER,
        "standard": ArticleType.OTHER,
        "supplementary-materials": ArticleType.OTHER,
    }

    @staticmethod
    def _normalize_type(openalex_publication_type: str | None) -> ArticleType | None:
        """Normalize OpenAlex publication type to ArticleType."""
        if openalex_publication_type is None:
            return None
        return OpenAlexSpider.TYPE_MAP.get(openalex_publication_type.lower())
