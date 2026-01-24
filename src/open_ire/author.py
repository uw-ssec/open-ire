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

    _email: str | None
    _parsed_name: HumanName = field(init=False, repr=False)

    def __init__(self, name: str | HumanName, email: str | None = None) -> None:
        if isinstance(name, str):
            self._parsed_name = HumanName(name)
        else:
            self._parsed_name = name
        self._email = email

    def __repr__(self) -> str:
        return f"AuthorRecord(name='{self._parsed_name}', email='{self._email}')"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, AuthorRecord):
            return NotImplemented
        return self.normalized_name == other.normalized_name and self.email == other.email

    def __hash__(self) -> int:
        return hash((self.normalized_name, self.email))

    @property
    def email(self) -> str | None:
        return self._email

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

    @property
    def full_name(self) -> str:
        """Return the full name as parsed by nameparser."""
        return str(self._parsed_name)

    @property
    def normalized_name(self) -> str:
        """Return normalized name in 'Last, First Middle' format for consistent storage."""
        last = self.last_name.strip()
        first = self.first_name.strip()
        middle = self.middle_name.strip()

        # Build given name (first + middle)
        given_parts = [p for p in [first, middle] if p]
        given_name = " ".join(given_parts) if given_parts else ""

        # Return in "Last, First Middle" format
        if last and given_name:
            return f"{last}, {given_name}"
        if last:
            return last
        if given_name:
            return given_name
        return str(self._parsed_name).strip() or "Unknown"

    @classmethod
    def parse_author_string(cls, author_string: str) -> list["AuthorRecord"]:
        """Parse a semicolon-separated author string into AuthorRecord objects.

        Authors are separated by semicolons. Each author name is parsed by nameparser
        which handles both "Last, First" and "First Last" formats.

        Args:
            author_string: Semicolon-separated string of author names

        Returns:
            List of AuthorRecord objects (with empty email fields)
        """
        if not author_string or not author_string.strip():
            return []

        # Split by semicolon and parse each name
        parts = [name.strip() for name in author_string.split(";") if name.strip()]
        return [cls(name=name, email="") for name in parts]


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
