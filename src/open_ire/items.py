from __future__ import annotations

from dataclasses import dataclass


@dataclass
class OpenIreItem:
    """Scrapy item to include full-text article metadata and related files.

    Attributes
    ----------
    authors:
        Names of the authors or creators of the resource.
    file_urls:
        List of URLs linking the downloadable files associated with the article.
    publication_date:
        Date when the article was published.
    reference:
        Unique identifier or reference number for the resource within the repository.
    repository:
        Name of the repository where the resource was found (e.g., "eric").
    title:
        Title of the article.
    url:
        Full URL to the article's page on the repository.
    abstract:
        Summary or abstract of the resource, if available.
    doi:
        Digital Object Identifier, if assigned.
    eissn:
        Electronic International Standard Serial Number, if available.
    files:
        List of downloaded files, populated during the download process.
    isbn:
        International Standard Book Number, if assigned.
    issn:
        International Standard Serial Number, if assigned.
    """
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