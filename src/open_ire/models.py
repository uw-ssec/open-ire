import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlalchemy.ext.hybrid import hybrid_property
from sqlmodel import Field, Relationship, SQLModel

from open_ire.enums import OAEvidenceKind, OAStatus


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
    oa_evidence: list["ArticleOAEvidence"] = Relationship(back_populates="article")
    oa_status_transitions: list["ArticleOAStatusTransition"] = Relationship(
        back_populates="article"
    )

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

    @hybrid_property
    def oa_status(self) -> OAStatus | None:
        if not self.oa_status_transitions:
            return None

        latest = max(self.oa_status_transitions, key=lambda t: t.changed_at)

        return latest.to_status


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


class ArticleOAEvidence(SQLModel, table=True):
    """SQLModel to store evidence used to determine OA status.

    Attributes
    ----------
    id: Primary key for the database.
    article_id: Foreign key to the Article table.
    created_at: Datetime when the evidence was recorded.
    kind: Evidence category (license, external_oa, version, manual, faculty_author).
    supports_oa: Whether this evidence supports OA status.
    source: Origin of the evidence (e.g., "crossref", "openalex", "manual").
    data: Source-specific payload for evidence details.
    """

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    article_id: uuid.UUID = Field(foreign_key="article.id", index=True)
    created_at: datetime = Field(default_factory=datetime.now, index=True)

    kind: OAEvidenceKind = Field(index=True)
    supports_oa: bool
    source: str | None = None
    data: dict[str, Any] = Field(sa_column=Column(JSON), default_factory=dict)

    article: Article | None = Relationship(back_populates="oa_evidence")


class ArticleOAStatusTransition(SQLModel, table=True):
    """SQLModel to store OA status transitions for articles.

    Attributes
    ----------
    id: Primary key for the database.
    article_id: Foreign key to the Article table.
    from_status: Previous OA status (published, ready, partial, or None).
    to_status: New OA status (published, ready, partial, or None).
    changed_at: Datetime when the status transition was recorded.
    rule_version: Ruleset version used to compute the transition.
    reason_codes: Rule or factor identifiers applied in the decision.
    """

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    article_id: uuid.UUID = Field(foreign_key="article.id", index=True)

    from_status: OAStatus | None = Field(default=None, index=True)
    to_status: OAStatus | None = Field(default=None, index=True)
    changed_at: datetime = Field(default_factory=datetime.now, index=True)

    rule_version: str | None = None
    reason_codes: list[str] = Field(default_factory=list, sa_column=Column(JSON))

    article: Article | None = Relationship(back_populates="oa_status_transitions")
