import csv
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from rapidfuzz import fuzz


@dataclass(frozen=True, slots=True)
class AuthorRecord:
    """
    Represents an author with email and name information.
    """

    email: str
    first_name: str
    last_name: str

    @property
    def openalex_name(self) -> str:
        """Return name in 'Firstname Lastname' format for OpenAlex API."""
        return f"{self.first_name.strip()} {self.last_name.strip()}".strip()

    @property
    def wos_name(self) -> str:
        """Return name in 'LASTNAME FIRSTNAME' format for Web of Science API."""
        return f"{self.last_name.strip().upper()} {self.first_name.strip().upper()}".strip()


class AuthorIndex:
    """
    Loads and indexes author data from CSV files.

    Creates lookup tables for different repository formats and provides
    normalized text matching capabilities for author name resolution.
    """

    _required_fields = frozenset({"FirstName", "LastName", "Email"})

    def __init__(self, csv_path: Path) -> None:
        self.path = csv_path
        self.records = self._load_records()
        self.lookups = self._build_lookups()

    # === PUBLIC INTERFACE ===

    def get_lookup(self, repository: str) -> dict[str, dict[str, str]]:
        """Get lookup tables for a specific repository format."""
        if repository not in self.lookups:
            msg = f"Unsupported author lookup repository: {repository}"
            raise ValueError(msg)

        return self.lookups[repository]

    # === DATA LOADING AND PROCESSING ===

    def _load_records(self) -> list[AuthorRecord]:
        """Load and validate author records from CSV file."""
        if not self.path.exists():
            msg = f"Author file not found: {self.path}"
            raise FileNotFoundError(msg)

        records: list[AuthorRecord] = []
        with self.path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or not self._required_fields.issubset(reader.fieldnames):
                msg = (
                    f"Author file must include columns: {', '.join(sorted(self._required_fields))}"
                )
                raise ValueError(msg)

            for row in reader:
                record = self._build_record(
                    row.get("FirstName"), row.get("LastName"), row.get("Email")
                )
                if record:
                    records.append(record)

        if not records:
            msg = f"No valid author records found in {self.path}"
            raise ValueError(msg)

        return records

    def _build_record(
        self,
        first_name: str | None,
        last_name: str | None,
        email: str | None,
    ) -> AuthorRecord | None:
        """Build an AuthorRecord from CSV row data, returning None if invalid."""
        clean_email = self._stip_value(email)
        clean_first = self._stip_value(first_name)
        clean_last = self._stip_value(last_name)

        if not any((clean_email, clean_first, clean_last)):
            return None

        return AuthorRecord(first_name=clean_first, last_name=clean_last, email=clean_email)

    # === LOOKUP TABLE CONSTRUCTION ===

    def _build_lookups(self) -> dict[str, dict[str, dict[str, str]]]:
        """Build lookup tables for all supported repository formats."""
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

    def _build_lookup(self, attr: str) -> dict[str, str]:
        """Build a lookup table mapping names to emails for a specific name format."""
        lookup: dict[str, str] = {}
        for record in self.records:
            key = getattr(record, attr)
            if key and key not in lookup:
                lookup[key] = record.email

        return lookup

    # === TEXT PROCESSING UTILITIES ===

    @staticmethod
    def _stip_value(value: str | None) -> str:
        """Strip whitespace from a string value, handling None."""
        return (value or "").strip()

    @staticmethod
    def _normalize_text(value: str) -> str:
        """Normalize text for consistent matching by removing accents and standardizing case."""
        normalized = unicodedata.normalize("NFKD", value or "")
        normalized = "".join(c for c in normalized if c.isalnum() or c.isspace())

        return " ".join(normalized.lower().split())


class AuthorMatcher:
    """
    Matches author names against an author index using fuzzy string matching.

    Provides exact and similarity-based matching for author name resolution,
    supporting different repository name formats and configurable similarity thresholds.
    """

    def __init__(self, author_csv_path: str, repository: str, similarity_threshold: float = 0.9):
        self.similarity_threshold = similarity_threshold
        author_index = AuthorIndex(Path(author_csv_path).resolve())
        self.author_lookup = author_index.get_lookup(repository)

    # === PUBLIC MATCHING INTERFACE ===

    def match_author(self, candidate: str) -> tuple[str | None, str | None]:
        """Match a single author name, returning (matched_name, email) or (None, None)."""
        return self._match_from_lookup(
            candidate, self.author_lookup["raw"], self.author_lookup["normalized"]
        )

    def collect_matches(self, names: list[str]) -> tuple[list[str], list[str]]:
        """Match multiple author names, returning sorted lists of unique matches."""
        matched_names: set[str] = set()
        matched_emails: set[str] = set()

        for name in names:
            matched_name, matched_email = self.match_author(name)
            if matched_name:
                matched_names.add(matched_name)
            if matched_email:
                matched_emails.add(matched_email)

        return sorted(matched_names), sorted(matched_emails)

    # === MATCHING IMPLEMENTATION ===

    def _match_from_lookup(
        self, candidate: str, lookup: dict[str, str], normalized_lookup: dict[str, str]
    ) -> tuple[str | None, str | None]:
        """Match a candidate name against lookup tables using exact and fuzzy matching."""
        normalized_candidate = self._normalize_text(candidate)

        # Try exact match first
        if normalized_candidate in normalized_lookup:
            original = normalized_lookup[normalized_candidate]
            return original, lookup[original]

        # Fall back to fuzzy matching
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

    # === TEXT PROCESSING UTILITIES ===

    @staticmethod
    def _normalize_text(value: str) -> str:
        """Normalize text for consistent matching by removing accents and standardizing case."""
        normalized = unicodedata.normalize("NFKD", value or "")
        normalized = "".join(ch for ch in normalized if ch.isalnum() or ch.isspace())
        return " ".join(normalized.lower().split())

    @staticmethod
    def _similarity(left: str, right: str) -> float:
        """Calculate similarity score between two strings using token set ratio."""
        if not left or not right:
            return 0.0
        return fuzz.token_set_ratio(left, right) / 100
