import csv
import unicodedata
from dataclasses import field
from pathlib import Path

from nameparser import HumanName


class AuthorRecord:
    """
    Represents an author with email and parsed name information.

    The name is parsed using the nameparser library, which handles complex names
    including titles, middle names, and suffixes. Spiders can access individual
    name components to format names as required by their target APIs.
    """

    name: str | HumanName
    email: str | None
    _parsed_name: HumanName = field(init=False, repr=False)

    def __init__(self, name: str | HumanName, email: str | None = None) -> None:
        if isinstance(name, str):
            self._parsed_name = HumanName(name)
        else:
            self._parsed_name = name
            self.name = str(name)
        self.email = email

    def __repr__(self) -> str:
        return f"AuthorRecord(name='{self._parsed_name}', email='{self.email}')"

    @property
    def first_name(self) -> str:
        return str(self._parsed_name.first)

    @property
    def last_name(self) -> str:
        return str(self._parsed_name.last)

    @property
    def middle_name(self) -> str:
        return str(self._parsed_name.middle)

    @property
    def middle_names(self) -> str:
        """Alias for middle_name(), kept for consistency with middle_initials()."""
        return str(self._parsed_name.middle)

    @property
    def first_initial(self) -> str:
        """First initial (uppercase)."""
        first = str(self._parsed_name.first)
        return first[0].upper() if first else ""

    @property
    def middle_initial(self) -> str:
        """Middle initial (uppercase)."""
        middle = str(self._parsed_name.middle)
        return middle[0].upper() if middle else ""

    @property
    def middle_initials(self) -> str:
        """Middle initials (uppercase) joined together."""
        middle = str(self._parsed_name.middle)
        if not middle:
            return ""
        return "".join(initial[0].upper() for initial in middle.split(" ") if initial)

    @property
    def title(self) -> str:
        """Title component (e.g., Dr., Prof.)."""
        return str(self._parsed_name.title)

    @property
    def suffix(self) -> str:
        """Suffix component (e.g., Jr., III)."""
        return str(self._parsed_name.suffix)


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
        email = (email or "").strip()
        first_name = (first_name or "").strip()
        last_name = (last_name or "").strip()

        if not any((email, first_name, last_name)):
            return None

        # Construct full name from components and parse it
        full_name = f"{first_name} {last_name}"
        return AuthorRecord(email=email, name=HumanName(full_name))

    # === TEXT PROCESSING UTILITIES ===

    @staticmethod
    def _normalize_text(value: str) -> str:
        """Normalize text for consistent matching by removing accents and standardizing case."""
        normalized = unicodedata.normalize("NFKD", value or "")
        normalized = "".join(c for c in normalized if c.isalnum() or c.isspace())

        return " ".join(normalized.lower().split())
