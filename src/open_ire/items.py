from dataclasses import dataclass
from typing import Optional


@dataclass
class OpenIreItem:
    authors: str
    file_urls: list[str]
    publication_date: str
    reference: str
    repository: str
    title: str
    url: str

    abstract: Optional[str] = None
    doi: Optional[str] = None
    eissn: Optional[str] = None
    files: Optional[list] = None
    isbn: Optional[str] = None
    issn: Optional[str] = None
