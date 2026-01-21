import csv
import unicodedata
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AuthorRecord:
    """
    Represents an author with email and name information.
    """

    email: str
    first_name: str
    last_name: str


class AuthorIndex:
    """
    Loads and indexes author data from CSV files.
    """

    _required_fields = frozenset({"FirstName", "LastName", "Email"})

    def __init__(self, csv_path: Path) -> None:
        self.path = csv_path
        self.records = self._load_records()

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
