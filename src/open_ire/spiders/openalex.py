import csv
import json
import logging
from collections.abc import Generator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urlencode

from scrapy.http import Request, Response
from sqlmodel import Session, create_engine

from open_ire.author import ParsedAuthor
from open_ire.enums import ArticleType
from open_ire.items import ArticleItem, AuthorItem
from open_ire.pipelines.author_identifier_pipeline import find_author_by_name
from open_ire.settings import (
    OPEN_IRE_CONTACT_EMAIL,
    OPEN_IRE_DATABASE_FILE,
    OPEN_IRE_OPENALEX_AMBIGUOUS_AUTHORS_FILE,
    OPEN_IRE_OPENALEX_INSTITUTION_ID,
)
from open_ire.spiders.search import AuthorSearchSpider
from open_ire.utils import parse_date

logger: logging.Logger = logging.getLogger(__name__)


# === MODULE-LEVEL UTILITIES ===


def id_from_uri(uri: str) -> str:
    """Extract the ID part from a full URI (e.g., OpenAlex or ORCID).

    Examples:
        https://openalex.org/A5077779935 => A5077779935
        https://orcid.org/0000-0002-4664-9847 => 0000-0002-4664-9847

    Returns:
         Upcased ID string, or input string if the string is not a URI."""
    if not uri.startswith(("https://", "http://")):
        return uri
    return uri.split("/")[-1].upper()


# === OPENALEX API DATACLASSES ===


@dataclass(frozen=True, slots=True)
class OpenAlexInstitution:
    """
    Institution object from OpenAlex API.

    See https://docs.openalex.org/api-entities/institutions/institution-object
    """

    id: str
    display_name: str
    raw: dict[str, Any]

    @classmethod
    def from_api(cls, record: dict[str, Any]) -> "OpenAlexInstitution":
        return cls(
            id=record["id"],
            display_name=record["display_name"],
            raw=record,
        )

    def matches_institution(self, institution_id: str) -> bool:
        """Check if this institution matches the given ID (case-insensitive, URI-tolerant)."""
        return id_from_uri(self.id).casefold() == id_from_uri(institution_id).casefold()


@dataclass(frozen=True, slots=True)
class OpenAlexAffiliation:
    """
    Affiliation dictionary from OpenAlex API.

    See https://docs.openalex.org/api-entities/authors/author-object#affiliations
    """

    institution: OpenAlexInstitution
    years: list[int]
    raw: dict[str, Any]

    @classmethod
    def from_api(cls, record: dict[str, Any]) -> "OpenAlexAffiliation":
        return cls(
            institution=OpenAlexInstitution.from_api(record["institution"]),
            years=record["years"],
            raw=record,
        )


@dataclass(frozen=True, slots=True)
class OpenAlexAuthor:
    """
    Author object from OpenAlex API.

    See https://docs.openalex.org/api-entities/authors/author-object
    """

    id: str
    display_name: str
    orcid: str | None
    relevance_score: float
    works_count: int
    cited_by_count: int
    last_known_institutions: list[OpenAlexInstitution]
    affiliations: list[OpenAlexAffiliation]
    raw: dict[str, Any]

    @classmethod
    def from_api(cls, record: dict[str, Any]) -> "OpenAlexAuthor":
        return cls(
            id=record["id"],
            display_name=record["display_name"],
            orcid=record.get("orcid") or None,
            relevance_score=record.get("relevance_score") or 0.0,
            works_count=record.get("works_count") or 0,
            cited_by_count=record.get("cited_by_count") or 0,
            last_known_institutions=[
                OpenAlexInstitution.from_api(inst)
                for inst in record.get("last_known_institutions") or []
            ],
            affiliations=[
                OpenAlexAffiliation.from_api(affiliation)
                for affiliation in record.get("affiliations") or []
            ],
            raw=record,
        )

    def years_at_institution(self, institution_id: str) -> set[int]:
        """Extract the years of affiliation with an institution."""
        years: set[int] = set()
        for affiliation in self.affiliations:
            if not affiliation.institution.matches_institution(institution_id):
                continue
            years.update(affiliation.years)
        return years

    def identifiers(self) -> list[dict[str, str]]:
        """Return all known identifiers for this author from the OpenAlex `ids` dict."""
        result: list[dict[str, str]] = []
        for authority, identifier in (self.raw.get("ids") or {}).items():
            if identifier:
                result.append({"authority": authority, "identifier": identifier})
        return result


# === AMBIGUOUS AUTHOR TRACKING ===


@dataclass(frozen=True, slots=True)
class AmbiguousAuthor:
    """An author search that returned multiple candidates requiring manual disambiguation."""

    searched_author: str
    candidates: list[OpenAlexAuthor]
    ambiguity_reason: str

    FIELD_CHOICE: ClassVar[str] = "choice"
    FIELD_SEARCHED_AUTHOR: ClassVar[str] = "searched_author_name"
    FIELD_OPENALEX_ID: ClassVar[str] = "openalex_id"
    FIELD_ORCID: ClassVar[str] = "orcid"

    CSV_FIELDNAMES: ClassVar[list[str]] = [
        "choice",
        "timestamp",
        "searched_author_name",
        "candidate_rank",
        "candidate_count",
        "display_name",
        "openalex_id",
        "orcid",
        "relevance_score",
        "works_count",
        "cited_by_count",
        "years_affiliated_with_us",
        "last_known_institutions",
    ]

    def to_csv_rows(self, institution_id: str) -> list[dict[str, str]]:
        """Build CSV rows for all candidates."""
        rows: list[dict[str, str]] = []
        for rank, candidate in enumerate(self.candidates, start=1):
            our_years = candidate.years_at_institution(institution_id)
            row = {
                self.FIELD_CHOICE: "",
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                self.FIELD_SEARCHED_AUTHOR: self.searched_author,
                "candidate_rank": str(rank),
                "candidate_count": str(len(self.candidates)),
                "display_name": candidate.display_name,
                self.FIELD_OPENALEX_ID: candidate.id,
                self.FIELD_ORCID: candidate.orcid or "",
                "relevance_score": str(candidate.relevance_score),
                "works_count": str(candidate.works_count),
                "cited_by_count": str(candidate.cited_by_count),
                "years_affiliated_with_us": ",".join([str(y) for y in sorted(our_years)]),
                "last_known_institutions": ";".join(
                    inst.display_name for inst in candidate.last_known_institutions
                ),
            }
            rows.append(row)
        return rows


class AmbiguousAuthorList:
    """Manages the list of ambiguous authors and their CSV persistence."""

    TRUTHY_CHOICES: ClassVar[frozenset[str]] = frozenset({"yes", "y", "1", "true"})

    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.entries: list[AmbiguousAuthor] = []
        self.existing_csv_entries: set[tuple[str, str]] = set()
        self.resolved_choices: dict[str, list[dict[str, str]]] = {}
        self._load_resolved_choices()

    def append(self, entry: AmbiguousAuthor) -> None:
        self.entries.append(entry)

    def write(self, institution_id: str) -> None:
        """Append new ambiguous author rows to the CSV file."""
        unresolved = [aa for aa in self.entries if aa.searched_author not in self.resolved_choices]
        if not unresolved:
            self.entries.clear()
            return

        rows: list[dict[str, str]] = []
        for aa in unresolved:
            for row in aa.to_csv_rows(institution_id):
                key = (
                    row[AmbiguousAuthor.FIELD_SEARCHED_AUTHOR],
                    row[AmbiguousAuthor.FIELD_OPENALEX_ID],
                )
                if key in self.existing_csv_entries:
                    continue
                rows.append(row)

        if not rows:
            self.entries.clear()
            return

        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        file_exists = self.file_path.exists()

        with self.file_path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, AmbiguousAuthor.CSV_FIELDNAMES)
            if not file_exists:
                writer.writeheader()
            writer.writerows(rows)

        unique_searched_authors = {
            row[AmbiguousAuthor.FIELD_SEARCHED_AUTHOR]
            for row in rows
            if row.get(AmbiguousAuthor.FIELD_SEARCHED_AUTHOR)
        }
        logger.warning(
            "Added %s ambiguous OpenAlex author(s) to %s",
            len(unique_searched_authors),
            self.file_path,
        )
        self.entries.clear()

    def _load_resolved_choices(self) -> None:
        """Read the ambiguous authors CSV and extract rows where the user filled in a choice."""
        if not self.file_path.exists():
            return

        with self.file_path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                searched_author = row.get(AmbiguousAuthor.FIELD_SEARCHED_AUTHOR, "")
                openalex_id = row.get(AmbiguousAuthor.FIELD_OPENALEX_ID, "")
                if not searched_author:
                    continue

                self.existing_csv_entries.add((searched_author, openalex_id))

                choice = (row.get(AmbiguousAuthor.FIELD_CHOICE) or "").strip().lower()
                if choice not in self.TRUTHY_CHOICES:
                    continue

                if searched_author not in self.resolved_choices:
                    self.resolved_choices[searched_author] = []

                self.resolved_choices[searched_author].append(
                    {
                        AmbiguousAuthor.FIELD_OPENALEX_ID: openalex_id,
                        AmbiguousAuthor.FIELD_ORCID: row.get(AmbiguousAuthor.FIELD_ORCID, ""),
                    }
                )

        if self.resolved_choices:
            logger.info(
                "Loaded pre-resolved choices for %s author(s) from %s",
                len(self.resolved_choices),
                self.file_path,
            )


# === SPIDER ===


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

        self.items_generated = {
            "ArticleItem": 0,
            "AuthorItem": 0,
        }
        self.ambiguous_authors = AmbiguousAuthorList(Path(OPEN_IRE_OPENALEX_AMBIGUOUS_AUTHORS_FILE))

    def author_name_for_query(self, record: ParsedAuthor) -> str:
        return " ".join(
            part for part in [record.first_name, record.middle_names, record.last_name] if part
        )

    def build_search_request(self, record: ParsedAuthor) -> Request:
        """Build a request for a given author record.

        If the author already has known OpenAlex IDs in the database, skip the
        author search and go straight to fetching publications. Otherwise, search
        for the author via the OpenAlex API.
        """
        searched_author = self.canonical_author_name(record)

        known_ids = self._find_known_openalex_ids(record)
        if known_ids:
            author_id_filter = "|".join(known_ids)
            self.logger.info(
                "Found %s known OpenAlex ID(s) for '%s'; skipping author search",
                len(known_ids),
                searched_author,
            )
            return self._build_publications_request(author_id_filter, searched_author)

        term = self.author_name_for_query(record)
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
            callback=self._parse_author_search_results,
            meta={"searched_author": searched_author},
        )

    def closed(self, _reason: str | None = None) -> None:
        self.ambiguous_authors.write(self.our_institution_id)

        logger.info(
            "Generated %s ArticleItem(s) and %s AuthorItem(s)",
            self.items_generated["ArticleItem"],
            self.items_generated["AuthorItem"],
        )

    # === HIGH-LEVEL WORKFLOW METHODS ===

    def _parse_author_search_results(
        self, response: Response
    ) -> Generator[Request | AuthorItem, None, None]:
        """Parse author search results and yield AuthorItems and publications requests."""
        searched_author = response.meta["searched_author"]
        raw = json.loads(response.text or "{}")
        authors = [OpenAlexAuthor.from_api(result) for result in raw.get("results", [])]

        if not authors:
            self.logger.warning("No authors found matching '%s'", searched_author)
            return

        # If ambiguous authors were manually resolved, just go with those results.
        if searched_author in self.ambiguous_authors.resolved_choices:
            yield from self._resolved_author_search_results(searched_author)
            return

        # Single result or disambiguate among multiple.
        the_author = self._disambiguate_authors(searched_author, authors)
        if the_author is None:
            return

        self.items_generated["AuthorItem"] += 1
        yield self._build_author_item(searched_author, the_author)
        yield self._build_publications_request(the_author.id, searched_author)

    def _resolved_author_search_results(
        self, searched_author: str
    ) -> Generator[Request | AuthorItem, None, None]:
        """Yield results for a manually resolved ambiguous author from the CSV."""
        chosen = self.ambiguous_authors.resolved_choices[searched_author]
        self.logger.info(
            "Using %s pre-resolved choice(s) for '%s'",
            len(chosen),
            searched_author,
        )
        yield self._build_author_item_from_choices(searched_author, chosen)
        for choice in chosen:
            openalex_id = choice[AmbiguousAuthor.FIELD_OPENALEX_ID]
            if openalex_id:
                yield self._build_publications_request(openalex_id, searched_author)

    def _disambiguate_authors(
        self, searched_author: str, authors: list[OpenAlexAuthor]
    ) -> OpenAlexAuthor | None:
        """Try to identify a single author from multiple OpenAlex results.

        Applies two filters in sequence:
        1. Name match — discard candidates whose display_name doesn't plausibly
           match the searched author (catches composite OpenAlex records).
        2. Institutional affiliation — among name matches, keep only those
           affiliated with our institution.

        Returns the author if unambiguous, or None if the search is ambiguous
        (in which case the author is recorded for manual resolution).
        """
        if len(authors) == 1:
            return authors[0]

        self.logger.info("Found %s authors matching '%s'", len(authors), searched_author)

        parsed_searched = ParsedAuthor(searched_author)
        name_matches = [
            au for au in authors if ParsedAuthor(au.display_name).likely_same(parsed_searched)
        ]
        if len(name_matches) == 1:
            self.logger.info(
                "Disambiguated '%s' to '%s' (ID: %s) based on name match",
                searched_author,
                name_matches[0].display_name,
                name_matches[0].id,
            )
            return name_matches[0]
        if not name_matches:
            self.ambiguous_authors.append(
                AmbiguousAuthor(
                    searched_author, authors, "no candidates with matching display name"
                )
            )
            return None

        affiliated = [au for au in name_matches if au.years_at_institution(self.our_institution_id)]

        if len(affiliated) == 1:
            self.logger.info(
                "Disambiguated '%s' to '%s' (ID: %s) based on institutional affiliation",
                searched_author,
                affiliated[0].display_name,
                affiliated[0].id,
            )
            return affiliated[0]

        self.ambiguous_authors.append(
            AmbiguousAuthor(
                searched_author,
                affiliated or name_matches,
                "no name-matched authors with institutional affiliation"
                if not affiliated
                else "multiple name-matched authors with institutional affiliation",
            )
        )
        return None

    def _build_publications_request(
        self, author_id: str, searched_author: str, cursor: str = "*"
    ) -> Request:
        """Build a request for an author's publications."""
        params = {
            "filter": f"author.id:{author_id},from_publication_date:{self.start_date}",
            "per_page": str(self.page_size),
            "cursor": cursor,
            "sort": "publication_date:desc",
        }
        url = f"{self.base_url}/works?{urlencode(params)}"

        self.logger.debug(
            "Requesting %spublications for %s (ID: %s)",
            "" if cursor == "*" else "next page of ",
            searched_author,
            id_from_uri(author_id),
        )

        return Request(
            url,
            headers=self.request_headers,
            callback=self._parse_publications,
            meta={"searched_author": searched_author, "cursor": cursor},
            cb_kwargs={"author_id": author_id},
        )

    def _build_author_item(self, searched_author: str, author: OpenAlexAuthor) -> AuthorItem:
        """Build an AuthorItem from our data and OpenAlex author data."""
        return AuthorItem(
            author=ParsedAuthor(searched_author),
            identifiers=author.identifiers(),
        )

    def _build_author_item_from_choices(
        self, searched_author: str, choices: list[dict[str, str]]
    ) -> AuthorItem:
        """Build an AuthorItem from pre-resolved CSV choices (may include multiple OpenAlex IDs)."""
        identifiers: list[dict[str, str]] = []
        seen: set[str] = set()

        for choice in choices:
            openalex_id = id_from_uri(choice.get(AmbiguousAuthor.FIELD_OPENALEX_ID, ""))
            if openalex_id and openalex_id not in seen:
                identifiers.append({"authority": "openalex", "identifier": openalex_id})
                seen.add(openalex_id)

            orcid = id_from_uri(choice.get(AmbiguousAuthor.FIELD_ORCID, ""))
            if orcid and orcid not in seen:
                identifiers.append({"authority": "orcid", "identifier": orcid})
                seen.add(orcid)

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
                    id_from_uri(author_id),
                )
            else:
                self.logger.info(
                    "Found %s publications for %s (ID: %s):",
                    total_count,
                    searched_author,
                    id_from_uri(author_id),
                )

        for _i, publication in enumerate(results):
            if not isinstance(publication, dict):
                continue

            if item := self._build_article_item(publication, searched_author):
                self.items_generated["ArticleItem"] += 1
                yield item

        if next_cursor := meta.get("next_cursor"):
            yield self._build_publications_request(author_id, searched_author, cursor=next_cursor)

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

    @staticmethod
    def _find_known_openalex_ids(author: ParsedAuthor) -> list[str]:
        """Look up existing OpenAlex IDs for an author in the database."""
        db_path = Path(OPEN_IRE_DATABASE_FILE)
        if not db_path.exists():
            return []

        engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
        try:
            with Session(engine) as session:
                db_author = find_author_by_name(session, author)
                if not db_author:
                    return []

                openalex_ids = []
                for ident in db_author.identifiers:
                    if ident.authority == "openalex":
                        openalex_ids.append(ident.identifier)
                return openalex_ids
        finally:
            engine.dispose()

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
