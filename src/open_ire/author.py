import csv
import unicodedata
from dataclasses import field
from pathlib import Path

from nameparser import HumanName


class ParsedAuthor:
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
        self._email = self._normalize_email(email)

    def __repr__(self) -> str:
        return f"ParsedAuthor(name='{self._parsed_name}', email='{self._email}')"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ParsedAuthor):
            return NotImplemented
        return self.canonical_name == other.canonical_name and self.email == other.email

    def __hash__(self) -> int:
        return hash((self.canonical_name, self.email))

    def __bool__(self) -> bool:
        return bool(self.canonical_name)

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
    def canonical_name(self) -> str:
        """Return the canonical name in 'Last, First Middle' format."""
        last = self.last_name.strip()
        first = self.first_name.strip()
        middle = self.middle_names.strip()

        # Build the given name (first + middle)
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

    @staticmethod
    def _normalize_name(s: str | None) -> str:
        """Normalize string for fuzzy matching: lowercase, remove diacritics and punctuation."""
        if not s:
            return ""
        # Decompose Unicode characters and filter out combining marks (accents)
        s = unicodedata.normalize("NFKD", s or "")
        # Keep only alphanumeric and spaces, then collapse whitespace
        s = "".join(c for c in s if c.isalnum() or c.isspace())
        return " ".join(s.lower().split())

    @staticmethod
    def _normalize_email(email: str | None) -> str | None:
        """Normalize email address for consistent storage and comparison."""
        if not email:
            return None
        return email.strip().lower()

    @staticmethod
    def _names_compatible(name1: str, name2: str) -> bool:
        """Check if two names are compatible (exact match, initial match, or prefix match)."""
        # If either name is empty, they're compatible (one person may not have that name component)
        if not name1 or not name2:
            return True

        if name1 == name2:
            return True

        # Remove periods and spaces for comparison
        name1_clean = name1.replace(".", "").replace(" ", "").strip()
        name2_clean = name2.replace(".", "").replace(" ", "").strip()

        if not name1_clean or not name2_clean:
            return True

        is_name1_initial = len(name1_clean) == 1
        is_name2_initial = len(name2_clean) == 1

        # If one is an initial, check if it matches the first letter of the other
        if is_name1_initial and not is_name2_initial:
            return name1_clean == name2_clean[0]
        if is_name2_initial and not is_name1_initial:
            return name2_clean == name1_clean[0]

        # For non-initials, check prefix matching (handles hyphenated names)
        # e.g., "Su" matches "Su-Ling" or "SuLing"
        return name1_clean.startswith(name2_clean) or name2_clean.startswith(name1_clean)

    def _emails_compatible(self, email1: str | None, email2: str | None) -> bool:
        """Check if two emails are compatible (both empty or matching)."""
        if not email1 or not email2:
            return True
        return self._normalize_email(email1) == self._normalize_email(email2)

    def likely_same(self, other: "ParsedAuthor") -> bool:
        """
        Determine if two ParsedAuthor instances likely represent the same person.

        This method handles cases where one name is a subset of another:
        - 'Welland, Sasha' and 'Welland, Sasha Su-Ling' -> True (likely same)
        - 'Welland, Sasha Su-Ling' and 'Welland, Sasha Mary' -> False (likely different)
        - 'Welland, S.' and 'Welland, Sasha' -> True (likely same)
        - 'Su-Ling Welland' and 'S. Welland' -> True (likely same)

        Rules:
        1. Last names must match exactly (case-insensitive, diacritic-insensitive)
        2. First names must be compatible:
           - Match exactly (case-insensitive, diacritic-insensitive), OR
           - One is an initial that matches the other's first letter, OR
           - One is a prefix of the other (e.g., "Su" matches "Su-Ling")
        3. If both have middle names, they must be compatible (same rules as first names)
        4. If emails are both present and non-empty, they must match
        """
        if not self._emails_compatible(self.email, other.email):
            return False

        if self._normalize_name(self.last_name) != self._normalize_name(other.last_name):
            return False

        if not self._names_compatible(
            self._normalize_name(self.first_name), self._normalize_name(other.first_name)
        ):
            return False

        return self._names_compatible(
            self._normalize_name(self.middle_name), self._normalize_name(other.middle_name)
        )

    @classmethod
    def parse_author_string(cls, author_string: str) -> list["ParsedAuthor"]:
        """Parse a semicolon-separated author string into ParsedAuthor objects.

        Authors are separated by semicolons. Each author name is parsed by nameparser
        which handles both "Last, First" and "First Last" formats.

        Args:
            author_string: Semicolon-separated string of author names

        Returns:
            List of ParsedAuthor objects (with empty email fields)
        """
        if not author_string or not author_string.strip():
            return []

        # Split by semicolon and parse each name
        parts = [name.strip() for name in author_string.split(";") if name.strip()]
        return [cls(name=name, email="") for name in parts]

    @classmethod
    def encode_author_string(cls, authors: list["ParsedAuthor"]) -> str:
        """Encode a list of ParsedAuthor objects back into a semicolon-separated string."""
        return "; ".join(str(author.canonical_name) for author in authors)


class AuthorIndex:
    """
    Loads and indexes author data from CSV files.
    """

    _required_fields = frozenset({"FirstName", "LastName", "Email"})
    _optional_fields = frozenset({"MiddleNames"})

    def __init__(self, csv_path: Path) -> None:
        self.path = csv_path
        self.records = self._load_records()

    # === DATA LOADING AND PROCESSING ===

    def _load_records(self) -> list[ParsedAuthor]:
        """Load and validate author records from the CSV file."""
        if not self.path.exists():
            msg = f"Author file not found: {self.path}"
            raise FileNotFoundError(msg)

        records: list[ParsedAuthor] = []
        with self.path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or not self._required_fields.issubset(reader.fieldnames):
                msg = (
                    f"Author file must include columns: {', '.join(sorted(self._required_fields))}"
                )
                raise ValueError(msg)

            for row in reader:
                record = self._build_record(
                    row.get("FirstName"),
                    row.get("MiddleNames") or "",
                    row.get("LastName"),
                    row.get("Email"),
                )
                if record:
                    records.append(record)

        if not records:
            msg = f"No valid author records found in {self.path}"
            raise ValueError(msg)

        return records

    @staticmethod
    def _build_record(
        first_name: str | None,
        middle_names: str | None,
        last_name: str | None,
        email: str | None,
    ) -> ParsedAuthor | None:
        """Build a ParsedAuthor from CSV row data, returning None if invalid."""
        email = (email or "").strip()
        first_name = (first_name or "").strip()
        middle_names = (middle_names or "").strip()
        last_name = (last_name or "").strip()

        if not any((email, first_name, middle_names, last_name)):
            return None

        # Construct a full name from components and parse it
        full_name = " ".join([first_name, middle_names, last_name])
        return ParsedAuthor(HumanName(full_name), email)
