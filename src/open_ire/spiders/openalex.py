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
        self.our_institution_id = OPENALEX_INSTITUTION_ID.strip().upper()
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
            author_id = self._extract_author_id(author, matched_author)

            # TODO: OpenAlex returns a relevance score; we could use it for early filtering.
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
                the_author = self._disambiguate_authors(authors, matched_author)
            except AmbiguousAuthorError as e:
                self.logger.warning("%s", e)
                self._add_to_ambiguous_authors(matched_author, e.candidates, e.reason)
                return

        yield self._build_author_item(matched_author, the_author)

        author_id = self._extract_author_id(the_author, matched_author)
        yield from self._request_author_publications(author_id, matched_author)

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
            self._id_from_uri(author_id),
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
                    "identifier": self._id_from_uri(openalex_id),
                }
            )

        if orcid_url := author_data.get("orcid"):
            identifiers.append({"authority": "orcid", "identifier": self._id_from_uri(orcid_url)})

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
                    self._id_from_uri(author_id),
                )
            else:
                self.logger.info(
                    "Found %s publications for %s (ID: %s):",
                    total_count,
                    matched_author,
                    self._id_from_uri(author_id),
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

    # === AUTHOR DISAMBIGUATION ===

    Institution = namedtuple("Institution", ["id", "name"])
    Affiliation = namedtuple("Affiliation", ["institution", "years"])

    def _disambiguate_authors(
        self, authors: list[dict[str, Any]], matched_author: str
    ) -> dict[str, Any]:
        """Attempt to disambiguate multiple author matches by recent institutional affiliation.

        Returns a single-element list if disambiguation succeeds.
        Raises AmbiguousAuthorError if disambiguation fails.
        """
        affiliated_authors = []

        for author_record in authors:
            affiliations = self._extract_affiliations(author_record)
            institution_years = self._years_at_institution(self.our_institution_id, affiliations)
            if not institution_years:
                continue
            affiliated_authors.append(author_record)

        if not affiliated_authors or len(affiliated_authors) > 1:
            rough_number = "no" if not affiliated_authors else "multiple"
            raise AmbiguousAuthorError(
                author_name=matched_author,
                candidates=affiliated_authors,
                reason=f"{rough_number} authors with institutional affiliation",
            )

        the_author = affiliated_authors[0]
        self.logger.info(
            "Disambiguated '%s' to '%s' (ID: %s) based on recent institutional affiliation",
            matched_author,
            the_author.get("display_name"),
            self._id_from_uri(self._extract_author_id(the_author, matched_author)),
        )
        return the_author

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

            for rank, author in enumerate(candidates, start=1):
                row = self._build_ambiguous_authors_file_row(
                    matched_author=matched_author,
                    author_record=author,
                    rank=rank,
                    candidate_count=candidate_count,
                    reason=reason,
                    start_year=start_year,
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

        unique_matched_authors = {
            row["matched_author"] for row in rows if row.get("matched_author")
        }
        self.logger.warning(
            "Added %s ambiguous OpenAlex author(s) to %s",
            len(unique_matched_authors),
            self.ambiguous_authors_file,
        )
        self._ambiguous_authors.clear()

    def _build_ambiguous_authors_file_row(
        self,
        matched_author: str,
        author_record: dict[str, Any],
        rank: int,
        candidate_count: int,
        reason: str,
        start_year: int,
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
            "matched_author": matched_author,
            "candidate_rank": str(rank),
            "candidate_count": str(candidate_count),
            "ambiguity_reason": reason,
            "start_year": str(start_year),
            "openalex_id": self._id_from_uri(openalex_id),
            "openalex_url": openalex_id,
            "display_name": str(author_record.get("display_name", "")),
            "orcid": self._id_from_uri(str(author_record.get("orcid", ""))),
            "relevance_score": str(author_record.get("relevance_score", -1)),
            "works_count": str(author_record.get("works_count", -1)),
            "cited_by_count": str(author_record.get("cited_by_count", -1)),
            "years_affiliated": ",".join([str(y) for y in institution_years]),
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
    def _extract_institution(record: dict[str, Any]) -> Institution:
        """Extract institutions from the institution record."""
        institution_id = record.get("id", "")
        institution_name = record.get("display_name", "")
        return OpenAlexSpider.Institution(institution_id, institution_name)

    # === HELPER METHODS ===

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
    def _extract_author_id(author_data: dict[str, Any], matched_author: str) -> str:
        author_id = author_data.get("id")
        if not author_id:
            msg = f"Author match for '{matched_author}' has no ID: {author_data}"
            raise ValueError(msg)
        assert isinstance(author_id, str)
        return author_id

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
    def _same_institution(institution_a: str | None, institution_b: str | None) -> bool:
        if not institution_a or not institution_b:
            return False
        return (
            OpenAlexSpider._id_from_uri(institution_a).casefold()
            == OpenAlexSpider._id_from_uri(institution_b).casefold()
        )

    @staticmethod
    def _normalize_type(raw_type: str | None) -> ArticleType | None:
        """Normalize OpenAlex publication type to ArticleType."""
        if raw_type is None:
            return None
        return OpenAlexSpider.TYPE_MAP.get(raw_type.lower())
