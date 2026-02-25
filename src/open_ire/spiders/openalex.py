import json
from collections.abc import Generator
from typing import Any, ClassVar
from urllib.parse import urlencode

from scrapy.http import Request, Response

from open_ire.author import ParsedAuthor
from open_ire.enums import ArticleType
from open_ire.items import ArticleItem
from open_ire.settings import OPEN_IRE_CONTACT_EMAIL, OPENALEX_INSTITUTION_ID
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

    @classmethod
    def _normalize_type(cls, raw_type: str | None) -> ArticleType | None:
        """Normalize OpenAlex publication type to ArticleType."""
        if raw_type is None:
            return None
        return cls.TYPE_MAP.get(raw_type.lower())

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

    def _get_author_name(self, record: ParsedAuthor) -> str:
        return " ".join(
            part for part in [record.first_name, record.middle_names, record.last_name] if part
        )

    # === HIGH-LEVEL WORKFLOW METHODS ===
    # These methods define the main crawling workflow

    def build_search_request(self, term: str) -> Request:
        """Build the initial search request for a given author name."""
        params = {
            "search": term,
            "filter": f"affiliations.institution.id:{self.institution_id}",
            "per_page": str(self.page_size),
        }
        url = f"{self.base_url}/authors?{urlencode(params)}"

        return Request(
            url,
            headers=self.request_headers,
            callback=self.author_publication_requests,
            meta={"matched_author": term},
        )

    def author_publication_requests(self, response: Response) -> Generator[Request, None, None]:
        """Parse author search results and generate publication requests."""
        matched_author = response.meta["matched_author"]
        data = json.loads(response.text or "{}")

        for author in data.get("results", []):
            author_id = author.get("id")
            if not author_id:
                continue

            # TODO: OpenAlex returns a relevance score; we could use it for early filtering.

            yield from self._request_publications(author_id, matched_author)

    # === SUPPORTING WORKFLOW METHODS ===
    # These methods support the main workflow

    def _request_publications(
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

        yield Request(
            url,
            headers=self.request_headers,
            callback=self.parse_publications,
            meta={"matched_author": matched_author},
            cb_kwargs={"author_id": author_id},
        )

    def parse_publications(
        self, response: Response, author_id: str
    ) -> Generator[Request | ArticleItem, None, None]:
        """Parse publication results and yield ArticleItems, handling pagination."""
        matched_author = response.meta["matched_author"]
        data = json.loads(response.text or "{}")
        results = data.get("results", [])

        for publication in results:
            if not isinstance(publication, dict):
                continue

            if item := self._build_item(publication, matched_author):
                yield item

        meta = data.get("meta", {})
        if next_cursor := meta.get("next_cursor"):
            yield from self._request_publications(author_id, matched_author, cursor=next_cursor)

    def _build_item(self, publication: dict[str, Any], matched_author: str) -> ArticleItem | None:
        """Build an ArticleItem from OpenAlex publication data."""
        external_id = publication.get("id")
        if not external_id:
            return None

        authors = self._extract_authors(publication)
        oa_status = publication.get("open_access", {}).get("oa_status")
        is_oa = publication.get("open_access", {}).get("is_oa")

        raw_type = publication.get("type")
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
            title=publication.get("title"),
            type=self._normalize_type(raw_type),
            url=publication.get("doi"),
        )

    # === DATA EXTRACTION UTILITIES ===
    # These methods extract specific data from OpenAlex API responses

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
