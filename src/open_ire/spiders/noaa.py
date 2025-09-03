from collections.abc import AsyncIterator
from datetime import date
from types import MappingProxyType
from typing import Any

import requests
from dateutil.parser import parse
from scrapy import Spider

from open_ire.items import ArticleItem
from open_ire.settings import OPEN_IRE_DEFAULT_TERMS

# See https://github.com/NOAA-Central-Library-NCL/NOAA_IR/blob/master/noaa_json_api/pandas-ir.ipynb for
# details on the document fields.


class NOAASpider(Spider):
    name = "noaa"

    EXTRA_FIELD_MAPPINGS = MappingProxyType(
        {
            "collection": ("rdf.isMemberOf",),
            "conference": ("mods.name_conference",),
            "journal_title": ("mods.related_original",),
            "keywords": ("mods.subject_topic", "keywords"),
            "publisher": ("mods.sm_publisher",),
            "type": ("mods.type_of_resource",),
            "volume": ("mods.volume",),
        }
    )
    SEARCHABLE_FIELDS = frozenset(
        {
            "keywords",
            "mods.abstract",
            "mods.name_corporate",
            "mods.name_personal",
            "mods.note",
            "mods.title",
        }
    )

    def __init__(
        self,
        terms: str = OPEN_IRE_DEFAULT_TERMS,
        page: str | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        if page:
            msg = "The NOAA spider does not support page parameter"
            raise ValueError(msg)

        super().__init__(*args, **kwargs)
        self.terms: list[str] = self._normalize_terms(terms)

    @staticmethod
    def _normalize_terms(terms: str) -> list[str]:
        return [term.strip().lower() for term in terms.split(",") if term.strip()]

    @staticmethod
    def _get_field_value(doc: dict[str, Any], fields: list[str]) -> Any:
        for field in fields:
            if doc.get(field):
                return doc[field]

        return None

    @staticmethod
    def _normalize_field_value(value: Any) -> str | None:
        if not value:
            return None

        if isinstance(value, list):
            return str(value[0]).strip() if value else None

        return str(value).strip()

    def _extract_authors(self, doc: dict[str, Any]) -> str | None:
        author_fields = ["mods.name_personal"]

        authors_value = self._get_field_value(doc, author_fields)
        if not authors_value:
            return None

        if isinstance(authors_value, list):
            authors_list = [str(author).strip() for author in authors_value if author]
        else:
            authors_list = [str(authors_value).strip()]

        return ", ".join(authors_list) if authors_list else None

    def _extract_publication_date(self, doc: dict[str, Any]) -> date | None:
        date_fields = ["mods.ss_publishyear"]

        for field in date_fields:
            date_value = self._get_field_value(doc, [field])
            if not date_value:
                continue

            date_text = self._normalize_field_value(date_value) or ""
            try:
                return parse(date_text).date()
            except (ValueError, TypeError):
                continue

        return None

    def _extract_extra_details(self, doc: dict[str, Any]) -> dict[str, Any]:
        extra: dict[str, Any] = {}

        for extra_key, doc_fields in self.EXTRA_FIELD_MAPPINGS.items():
            value = self._get_field_value(doc, list(doc_fields))
            if not value:
                continue

            if extra_key == "keywords":
                extra[extra_key] = self._extract_keywords(value)
            else:
                normalized_value = self._normalize_field_value(value)
                if normalized_value:
                    extra[extra_key] = normalized_value

        return extra

    @staticmethod
    def _extract_keywords(value: Any) -> list[str]:
        if isinstance(value, list):
            keywords = {str(k).strip() for k in value if k and str(k).strip()}
        else:
            keywords = {str(value).strip()} if str(value).strip() else set()

        return list(keywords)

    def _document_matches_terms(self, doc: dict[str, Any]) -> bool:
        if not self.terms:
            return True

        searchable_text = self._build_searchable_text(doc)
        return any(term in searchable_text for term in self.terms)

    def _build_searchable_text(self, doc: dict[str, Any]) -> str:
        text_parts: list[str] = []

        for field in self.SEARCHABLE_FIELDS:
            if field not in doc or not doc[field]:
                continue

            value = doc[field]
            if isinstance(value, list):
                text_parts.extend(str(v).lower() for v in value if v)
            else:
                text_parts.append(str(value).lower())

        return " ".join(text_parts)

    def _create_article_item(self, doc: dict[str, Any]) -> ArticleItem:
        reference = doc.get("PID", "").split(":")[-1]

        title = self._normalize_field_value(self._get_field_value(doc, ["mods.title"]))
        abstract = self._normalize_field_value(self._get_field_value(doc, ["mods.abstract"]))
        doi = self._normalize_field_value(
            self._get_field_value(doc, ["mods.sm_digital_object_identifier"])
        )
        issn = self._normalize_field_value(self._get_field_value(doc, ["mods.sm_issn"]))

        article_url = f"https://repository.library.noaa.gov/view/noaa/{reference}"
        file_urls = [f"{article_url}/noaa_{reference}_DS1.pdf"]

        return ArticleItem(
            abstract=abstract,
            authors=self._extract_authors(doc),
            doi=doi,
            extra=self._extract_extra_details(doc),
            file_urls=file_urls,
            issn=issn,
            publication_date=self._extract_publication_date(doc),
            reference=reference,
            repository=self.name,
            title=title or "",
            url=article_url,
        )

    async def start(self) -> AsyncIterator[Any]:
        r = requests.get(
            "https://repository.library.noaa.gov/fedora/export/download/collection/noaa"
        )
        r.raise_for_status()

        data = r.json()
        docs = data.get("response", {}).get("docs", [])

        for doc in docs:
            if self._document_matches_terms(doc):
                yield self._create_article_item(doc)
