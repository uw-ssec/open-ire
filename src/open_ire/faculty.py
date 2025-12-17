import csv
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from rapidfuzz import fuzz


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


class AuthorMatcher:
    def __init__(self, faculty_csv_path: str, repository: str, similarity_threshold: float = 0.9):
        self.similarity_threshold = similarity_threshold
        faculty_index = FacultyIndex(Path(faculty_csv_path).resolve())
        self.faculty_lookup = faculty_index.get_lookup(repository)

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

    def collect_matches(self, names: list[str]) -> tuple[list[str], list[str]]:
        matched_names: set[str] = set()
        matched_emails: set[str] = set()

        for name in names:
            matched_name, matched_email = self.match_author(name)
            if matched_name:
                matched_names.add(matched_name)
            if matched_email:
                matched_emails.add(matched_email)

        return sorted(matched_names), sorted(matched_emails)
