import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, Column, ForeignKey, Index, UniqueConstraint, text
from sqlalchemy.ext.hybrid import hybrid_property
from sqlmodel import Field, Relationship, SQLModel, select

from open_ire.enums import (
    ArticleType,
    ChangeOperation,
    DepositStatus,
    OAEvidenceKind,
    RunKind,
)


class ArticleBase(SQLModel):
    """Base SQLModel to define common article attributes.

    Attributes
    ----------
    abstract: Summary or abstract of the resource, if available.
    type: Normalized publication type (scholarly-article or other).
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
    type: ArticleType | None = None
    authors: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), index=True)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC), index=True)
    doi: str | None = None
    eissn: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)
    isbn: str | None = None
    issn: str | None = None
    publication_date: date | None = Field(default=None, index=True)
    reference: str = Field(index=True)
    repository: str = Field(index=True)
    title: str
    url: str


class Article(ArticleBase, table=True):
    """SQLModel to store article metadata."""

    __tablename__ = "article"

    model_config = {"ignored_types": (hybrid_property,)}

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)

    extra: dict[str, Any] = Field(sa_column=Column(JSON), default_factory=dict)
    files: list["ArticleFile"] = Relationship(back_populates="article")
    oa_evidence: list["ArticleOAEvidence"] = Relationship(back_populates="article")
    deposit_status_transitions: list["ArticleDepositStatusTransition"] = Relationship(
        back_populates="article"
    )

    # New author relationships
    authorships: list["Authorship"] = Relationship(
        back_populates="article", sa_relationship_kwargs={"passive_deletes": True}
    )

    __table_args__ = (
        UniqueConstraint("repository", "reference", name="uq_article_repository_reference"),
    )

    @property
    def files_size(self) -> int:
        """Total files size."""
        return sum(f.size for f in self.files if f.size)

    @hybrid_property
    def deposit_status(self) -> DepositStatus | None:
        if not self.deposit_status_transitions:
            return None

        latest = max(self.deposit_status_transitions, key=lambda t: t.changed_at)

        return latest.to_status

    @deposit_status.expression  # type: ignore[no-redef]
    def deposit_status(cls):
        return (
            select(ArticleDepositStatusTransition.to_status)
            .where(ArticleDepositStatusTransition.article_id == cls.id)
            .order_by(ArticleDepositStatusTransition.changed_at.desc())
            .limit(1)
            .scalar_subquery()
        )


class ArticleFile(SQLModel, table=True):
    """SQLModel to store downloaded files associated with articles.

    Attributes
    ----------
    id: Primary key for the ArticleFile table.
    article_id: Foreign key to the Article table.
    created_at: Datetime when the file metadata was added to this database.
    url: Original URL of the file.
    path: Local path where the file is stored.
    checksum: Checksum of the downloaded file.
    extension: File extension (without the dot).
    size: Size of the file in bytes.
    store_url: URL to the remote backup location (e.g., SharePoint).
    """

    __tablename__ = "article_file"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    article_id: uuid.UUID | None = Field(default=None, foreign_key="article.id")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), index=True)

    url: str = Field(unique=True)
    path: str
    checksum: str = Field()
    extension: str | None = None
    size: int | None = None
    store_url: str | None = None

    article: Article | None = Relationship(back_populates="files")


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

    __tablename__ = "article_oa_evidence"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    article_id: uuid.UUID = Field(foreign_key="article.id", index=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), index=True)

    kind: OAEvidenceKind = Field(index=True)
    supports_oa: bool
    source: str | None = None
    data: dict[str, Any] = Field(sa_column=Column(JSON), default_factory=dict)

    article: Article | None = Relationship(back_populates="oa_evidence")


class ArticleDepositStatusTransition(SQLModel, table=True):
    """SQLModel to store deposit status transitions for articles.

    Deposit status represents the readiness of the article to be deposited in ResearchWorks.
    This readiness may depend on licensing information that supports Open Access (OA) compliance.

    Attributes
    ----------
    id: Primary key for the database.
    article_id: Foreign key to the Article table.
    from_status: Previous deposit status (published, ready, partial, or None).
    to_status: New deposit status (published, ready, partial, or None).
    changed_at: Datetime when the status transition was recorded.
    reasons: Rule or factor identifiers applied in the decision.
    """

    __tablename__ = "article_deposit_status_transition"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    article_id: uuid.UUID = Field(foreign_key="article.id", index=True)

    from_status: DepositStatus | None = Field(default=None, index=True)
    to_status: DepositStatus | None = Field(default=None, index=True)
    changed_at: datetime = Field(default_factory=lambda: datetime.now(UTC), index=True)

    reasons: list[str] = Field(default_factory=list, sa_column=Column(JSON))

    article: Article | None = Relationship(back_populates="deposit_status_transitions")


class Author(SQLModel, table=True):
    """SQLModel to store author information.

    Attributes
    ----------
    first_name: Author's first name, if available.
    middle_names: Author's middle names, if available.
    last_name: Author's last name, if available.
    full_name: Complete author name as it appears in publications.
    uw_academic_unit: The academic unit of the author, if available.
    explicitly_searched: Boolean indicating if this author was explicitly searched.
    created_at: Datetime when the author was added to this database.
    updated_at: Datetime when the author was last updated in this database.
    """

    __tablename__ = "author"

    id: int | None = Field(default=None, primary_key=True)

    first_name: str | None = None
    middle_names: str | None = None
    last_name: str | None = None
    full_name: str = Field(index=True)
    canonical_name: str = Field(index=True)
    uw_academic_unit: str | None = Field(default=None, index=True)
    explicitly_searched: bool = Field(default=False, index=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # Relationships
    authorships: list["Authorship"] = Relationship(
        back_populates="author", sa_relationship_kwargs={"passive_deletes": True}
    )
    identifiers: list["AuthorIdentifier"] = Relationship(
        back_populates="author", sa_relationship_kwargs={"passive_deletes": True}
    )
    affiliations: list["AuthorAffiliation"] = Relationship(
        back_populates="author", sa_relationship_kwargs={"passive_deletes": True}
    )


class AuthorAffiliation(SQLModel, table=True):
    """SQLModel to store author affiliations.

    Attributes
    ----------
    author_id: Foreign key to the Author table.
    year: Year of the UW affiliation.
    """

    __tablename__ = "author_affiliation"

    id: int | None = Field(default=None, primary_key=True)

    author_id: int = Field(
        sa_column=Column(
            ForeignKey("author.id", ondelete="CASCADE"),
        ),
    )
    year: int = Field(ge=1900, index=True)

    # Relationships
    author: "Author" = Relationship(back_populates="affiliations")

    __table_args__ = (
        CheckConstraint("year >= 1900", name="ck_author_affiliation_year_gte_1900"),
        UniqueConstraint("author_id", "year", name="uq_author_affiliation_author_id_year"),
    )


class AuthorIdentifier(SQLModel, table=True):
    """SQLModel to store external identifiers for authors.

    Attributes
    ----------
    author_id: Foreign key to the Author table.
    authority: The organization or system that issued the identifier.
    identifier: The actual identifier value.
    created_at: Datetime when the identifier was added.
    """

    __tablename__ = "author_identifier"

    id: int | None = Field(default=None, primary_key=True)

    author_id: int = Field(
        sa_column=Column(
            ForeignKey("author.id", ondelete="CASCADE"),
        ),
    )

    authority: str = Field(index=True)
    identifier: str = Field(index=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # Relationships
    author: "Author" = Relationship(back_populates="identifiers")

    __table_args__ = (UniqueConstraint("authority", "identifier", name="uq_author_identifier"),)


class Authorship(SQLModel, table=True):
    """SQLModel to store many-to-many relationships between authors and articles.

    Attributes
    ----------
    article_id: Foreign key to the Article table.
    author_id: Foreign key to the Author table.
    author_order: Position of author in the publication's author list.
    created_at: Datetime when the relationship was created.
    """

    __tablename__ = "authorship"

    article_id: uuid.UUID = Field(
        sa_column=Column(
            ForeignKey("article.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    author_id: int = Field(
        sa_column=Column(
            ForeignKey("author.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    author_order: int | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # Relationships
    article: "Article" = Relationship(back_populates="authorships")
    author: "Author" = Relationship(back_populates="authorships")


class Run(SQLModel, table=True):
    """SQLModel to store one run: a crawl, a migration, or a session of manual edits.

    Every write to an audited table must happen while exactly one run is open
    (``finished_at`` is null): the database's audit triggers reject the write
    otherwise, and record it as a RowChange of the open run; other triggers keep
    a second run from opening. See ``open_ire.provenance``.

    Columns a person can't be expected to fill in by hand have database
    defaults, so a manual run needs only ``user`` and ``notes``.

    Attributes
    ----------
    id: Primary key; generated by the database if not given.
    kind: What opened the run; defaults to manual.
    user: Who made the run: the operating-system user, or a name typed by hand.
    notes: Why the run was made; required for manual runs.
    spider: Name of the spider, for crawls.
    command: Command line of the process that opened the run, shell-quoted.
    hostname: Machine the process ran on.
    version: Open IRE package version of the process.
    started_at: Datetime when the run was opened; defaults to now.
    finished_at: Datetime when the run was closed; null while it is open.
    finish_reason: How the run ended, such as Scrapy's close reason
        ("finished", "shutdown") or "failed".
    stats: Scrapy stats at close, made JSON-safe, for crawls.
    """

    __tablename__ = "run"

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        primary_key=True,
        sa_column_kwargs={"server_default": text("(lower(hex(randomblob(16))))")},
    )
    kind: RunKind = Field(default=RunKind.MANUAL, sa_column_kwargs={"server_default": "MANUAL"})
    user: str
    notes: str | None = None

    spider: str | None = None
    command: str | None = None
    hostname: str | None = None
    version: str | None = None

    started_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        index=True,
        sa_column_kwargs={"server_default": text("(strftime('%Y-%m-%d %H:%M:%f000', 'now'))")},
    )
    finished_at: datetime | None = None
    finish_reason: str | None = None
    stats: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))

    row_changes: list["RowChange"] = Relationship(back_populates="run")

    __table_args__ = (
        CheckConstraint(
            "kind <> 'MANUAL' OR trim(coalesce(notes, '')) <> ''", name="ck_run_manual_notes"
        ),
    )


class RowChange(SQLModel, table=True):
    """SQLModel to store one row a run inserted, updated, or deleted.

    Rows are written by the database's audit triggers, never by hand. Values are
    as SQLite stores them: UUIDs as 32-digit hex, datetimes as text, booleans as
    0 or 1.

    Attributes
    ----------
    id: Primary key; also orders changes within a run.
    run_id: Foreign key to the Run table.
    table_name: Table of the changed row.
    row_id: Primary key of the changed row as stored in the database;
        composite keys are comma-joined in key-column order.
    operation: Kind of write (insert, update, or delete).
    changes: Each column mapped to its ``[old, new]`` values: for updates, the
        changed columns except ``updated_at``; for deletes, every column, with
        ``new`` null; for inserts, empty.
    changed_at: Datetime of the write.
    """

    __tablename__ = "row_change"

    id: int | None = Field(default=None, primary_key=True)
    run_id: uuid.UUID = Field(foreign_key="run.id", index=True)

    table_name: str
    row_id: str
    operation: ChangeOperation
    changes: dict[str, list[Any]] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    changed_at: datetime = Field(default_factory=lambda: datetime.now(UTC), index=True)

    run: Run | None = Relationship(back_populates="row_changes")

    __table_args__ = (Index("ix_row_change_table_name_row_id", "table_name", "row_id"),)
