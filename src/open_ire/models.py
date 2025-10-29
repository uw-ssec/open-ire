import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, Relationship, SQLModel


class ArticleBase(SQLModel):
    """Base SQLModel to define common article attributes.

    Attributes
    ----------
    abstract: Summary or abstract of the resource, if available.
    authors: Names of the authors or creators of the resource.
    created_at: Datetime when the article was added to this database.
    updated_at: Datetime when the article was last updated in this database.
    doi: Digital Object Identifier, if assigned.
    eissn: Electronic International Standard Serial Number, if available.
    isbn: International Standard Book Number, if assigned.
    issn: International Standard Serial Number, if assigned.
    publication_date: Date when the article was published.
    reference: Unique identifier or reference number for the resource within the repository.
    repository: Name of the repository where the resource was found (e.g., "eric").
    title: Title of the article.
    url: Full URL to the article's page on the repository.
    """

    abstract: str | None = None
    authors: str | None = None
    created_at: datetime = Field(default_factory=datetime.now, index=True)
    updated_at: datetime = Field(default_factory=datetime.now, index=True)
    doi: str | None = None
    eissn: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)
    isbn: str | None = None
    issn: str | None = None
    publication_date: date = Field(index=True)
    reference: str = Field(index=True)
    repository: str = Field(index=True)
    title: str
    url: str


class Article(ArticleBase, table=True):
    """SQLModel to store article metadata."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)

    extra: dict[str, Any] = Field(sa_column=Column(JSON), default_factory=dict)
    files: list["ArticleFile"] = Relationship(back_populates="article")
    file_references: list["ArticleFileReference"] = Relationship(back_populates="article")

    __table_args__ = (
        UniqueConstraint("repository", "reference", name="uq_article_repository_reference"),
    )

    @property
    def files_size(self) -> int:
        """Total files size."""
        return sum(f.size for f in self.files if f.size)

    @property
    def file_references_size(self) -> int:
        """Total file references size."""
        return sum(f.size for f in self.file_references if f.size)


class ArticleFileBase(SQLModel):
    """Base SQLModel for common article file attributes.

    Attributes
    ----------
    article_id: Foreign key to the Article table.
    created_at: Datetime when the file metadata was added to this database.
    extension: File extension (without the dot).
    size: Size of the file in bytes.
    url: Original URL of the file.
    """

    article_id: uuid.UUID | None = Field(default=None, foreign_key="article.id")
    created_at: datetime = Field(default_factory=datetime.now, index=True)
    extension: str | None = None
    size: int | None = None
    url: str = Field(unique=True)


class ArticleFile(ArticleFileBase, table=True):
    """SQLModel to store downloaded files associated with articles.

    Attributes
    ----------
    id: Primary key for the database.
    checksum: Checksum of the downloaded file.
    path: Local path where the file is stored.
    store_url: URL to the remote backup location (e.g., SharePoint).
    """

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)

    checksum: str = Field()
    path: str
    store_url: str | None = None

    article: Article | None = Relationship(back_populates="files")


class ArticleFileReference(ArticleFileBase, table=True):
    """SQLModel to store references to external files with metadata only.

    This model is used for files that are not downloaded locally, but we want to track
    their metadata (e.g., size estimates from data.gov files).

    Attributes
    ----------
    id: Primary key for the database.
    source_url: URL of the website where the file `url` was found.
    """

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)

    source_url: str | None = None

    article: Article | None = Relationship(back_populates="file_references")


class OAPublicationBase(SQLModel):
    """Base SQLModel for OAP metadata."""

    authors: str = Field(index=True)
    created_at: datetime = Field(default_factory=datetime.now, index=True)
    doi: str = Field(index=True)
    external_id: str = Field(index=True)
    is_open_access: bool | None = None
    journal_name: str | None = None
    matched_author: str | None = None
    matched_email: str | None = None
    oa_status: str | None = None
    publication_date: date | None = Field(default=None, index=True)
    publication_type: str | None = None
    publication_year: int | None = Field(default=None, index=True)
    repository: str = Field(index=True)
    title: str
    updated_at: datetime = Field(default_factory=datetime.now, index=True)


class OAPublication(OAPublicationBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)

    __table_args__ = (
        UniqueConstraint(
            "repository", "external_id", name="uq_oap_publication_repository_external_id"
        ),
        UniqueConstraint("doi", name="uq_oap_publication_doi"),
    )
