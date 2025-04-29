from __future__ import annotations

from dataclasses import dataclass


@dataclass
class OpenIreItem:
    authors: str
    file_urls: list[str]
    publication_date: str
    reference: str
    repository: str
    title: str
    url: str

    abstract: str | None = None
    doi: str | None = None
    eissn: str | None = None
    files: list | None = None
    isbn: str | None = None
    issn: str | None = None
