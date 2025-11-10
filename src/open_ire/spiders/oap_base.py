import csv
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from dateutil.parser import parse
from rapidfuzz import fuzz
from scrapy import Spider


class OAPBaseSpider(Spider):
    name = "oap_base"
    page_size = 25
    similarity_threshold = 0.9

    def __init__(self, faculty_csv: str, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

        if self.name == "oap_base":
            msg = "OAPBaseSpider should not be used directly. Please use a subclass instead."
            raise ValueError(msg)

        if not faculty_csv:
            msg = "The 'faculty_csv' argument is required."
            raise ValueError(msg)

        self.repository_name = self.name.split("_")[-1]
        self.faculty_index = FacultyIndex(Path(faculty_csv).resolve())
        self.faculty_lookup = self.faculty_index.get_lookup(self.repository_name)

    @staticmethod
    def _normalize_text(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value or "")
        normalized = "".join(ch for ch in normalized if ch.isalnum() or ch.isspace())
        return " ".join(normalized.lower().split())

    @staticmethod
    def _similarity(left: str, right: str) -> float:
        if not left or not right:
            return 0.0

        return fuzz.token_set_ratio(left, right) / 100

    @staticmethod
    def _join_or_none(values: list[str]) -> str | None:
        return ", ".join(values) if values else None

    @staticmethod
    def _parse_year(value: Any) -> int | None:
        if value is None:
            return None

        try:
            return int(value)
        except (TypeError, ValueError):
            pass

        match = re.search(r"(19|20)\d{2}", str(value))
        if match:
            return int(match.group())

        return None

    @staticmethod
    def _parse_date(value: Any) -> date | None:
        if not value:
            return None

        try:
            parsed = parse(str(value))
        except (ValueError, TypeError):
            return None

        return parsed.date()

    def _match_from_lookup(
        self, candidate: str, lookup: dict[str, str], normalized_lookup: dict[str, str]
    ) -> tuple[str | None, str | None]:
        normalized_candidate = self._normalize_text(candidate)

        if normalized_candidate in normalized_lookup:
            original = normalized_lookup[normalized_candidate]
            return original, lookup[original]

        best_match: str | None = None
        best_score = 0.0

        for original in lookup:
            normalized_original = self._normalize_text(original)
            score = self._similarity(normalized_candidate, normalized_original)

            if score > best_score:
                best_match = original
                best_score = score

        if best_match and best_score >= self.similarity_threshold:
            return best_match, lookup[best_match]

        return None, None

    def match_author(self, candidate: str) -> tuple[str | None, str | None]:
        return self._match_from_lookup(
            candidate, self.faculty_lookup["raw"], self.faculty_lookup["normalized"]
        )

    def _collect_matches(
        self,
        names: list[str],
    ) -> tuple[list[str], list[str]]:
        matched_names: set[str] = set()
        matched_emails: set[str] = set()

        for name in names:
            matched_name, matched_email = self.match_author(name)
            if matched_name:
                matched_names.add(matched_name)

            if matched_email:
                matched_emails.add(matched_email)

        return list(matched_names), list(matched_emails)


@dataclass(frozen=True, slots=True)
class FacultyRecord:
    email: str
    first_name: str
    last_name: str

    @property
    def openalex_name(self) -> str:
        return f"{self.first_name.strip()} {self.last_name.strip()}".strip()

    @property
    def wos_name(self) -> str:
        return f"{self.last_name.strip().upper()} {self.first_name.strip().upper()}".strip()


class FacultyIndex:
    _required_fields = frozenset({"FirstName", "LastName", "Email"})

    def __init__(self, csv_path: Path) -> None:
        self.path = csv_path
        self.records = self._load_records()
        self.lookups = self._build_lookups()

    @staticmethod
    def _stip_value(value: str | None) -> str:
        return (value or "").strip()

    @staticmethod
    def _normalize_text(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value or "")
        normalized = "".join(c for c in normalized if c.isalnum() or c.isspace())

        return " ".join(normalized.lower().split())

    def _build_record(
        self,
        first_name: str | None,
        last_name: str | None,
        email: str | None,
    ) -> FacultyRecord | None:
        clean_email = self._stip_value(email)
        clean_first = self._stip_value(first_name)
        clean_last = self._stip_value(last_name)

        if not any((clean_email, clean_first, clean_last)):
            return None

        return FacultyRecord(first_name=clean_first, last_name=clean_last, email=clean_email)

    def _load_records(self) -> list[FacultyRecord]:
        if not self.path.exists():
            msg = f"Faculty file not found: {self.path}"
            raise FileNotFoundError(msg)

        records: list[FacultyRecord] = []
        with self.path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or not self._required_fields.issubset(reader.fieldnames):
                msg = (
                    f"Faculty file must include columns: {', '.join(sorted(self._required_fields))}"
                )
                raise ValueError(msg)

            for row in reader:
                record = self._build_record(
                    row.get("FirstName"), row.get("LastName"), row.get("Email")
                )
                if record:
                    records.append(record)

        if not records:
            msg = f"No valid faculty records found in {self.path}"
            raise ValueError(msg)

        return records

    def _build_lookup(self, attr: str) -> dict[str, str]:
        lookup: dict[str, str] = {}
        for record in self.records:
            key = getattr(record, attr)
            if key and key not in lookup:
                lookup[key] = record.email

        return lookup

    def _build_lookups(self) -> dict[str, dict[str, dict[str, str]]]:
        lookups: dict[str, dict[str, dict[str, str]]] = {}
        repository_attrs = {"openalex": "openalex_name", "wos": "wos_name"}

        for repository, attr in repository_attrs.items():
            raw = self._build_lookup(attr)
            normalized = {self._normalize_text(name): name for name in raw}

            lookups[repository] = {
                "raw": raw,
                "normalized": normalized,
            }

        return lookups

    def get_lookup(self, repository: str) -> dict[str, dict[str, str]]:
        if repository not in self.lookups:
            msg = f"Unsupported faculty lookup repository: {repository}"
            raise ValueError(msg)

        return self.lookups[repository]
