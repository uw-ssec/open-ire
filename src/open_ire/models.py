import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, Column, ForeignKey, UniqueConstraint
from sqlalchemy.ext.hybrid import hybrid_property
from sqlmodel import Field, Relationship, SQLModel, select

from open_ire.enums import ArticleType, DepositStatus, DepositWarrant


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
    created_at: datetime = Field(default_factory=datetime.now, index=True)
    updated_at: datetime = Field(default_factory=datetime.now, index=True)
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

    model_config = {"ignored_types": (hybrid_property,)}

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)

    extra: dict[str, Any] = Field(sa_column=Column(JSON), default_factory=dict)
    files: list["ArticleFile"] = Relationship(back_populates="article")
    file_references: list["ArticleFileReference"] = Relationship(back_populates="article")
    deposit_warrants: list["ArticleDepositWarrant"] = Relationship(back_populates="article")
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

    @property
    def file_references_size(self) -> int:
        """Total file references size."""
        return sum(f.size for f in self.file_references if f.size)

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


class ArticleDepositWarrant(SQLModel, table=True):
    """SQLModel to store warrants supporting an article's deposit eligibility.

    Attributes
    ----------
    id: Primary key for the database.
    article_id: Foreign key to the Article table.
    created_at: Datetime when the warrant was recorded.
    kind: Which warrant this row pertains to (license, external_oa, version, manual, faculty_author).
    supports_oa: Whether this warrant supports depositing the article.
    source: Origin of the warrant (e.g., "crossref", "datacite", "doaj", "manual").
    data: Source-specific payload for warrant details.
    """

    # Table renaming is deferred to a follow-up PR; pin the existing name so the
    # class rename is purely a code-level rename.
    __tablename__ = "articleoaevidence"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    article_id: uuid.UUID = Field(foreign_key="article.id", index=True)
    created_at: datetime = Field(default_factory=datetime.now, index=True)

    kind: DepositWarrant = Field(index=True)
    supports_oa: bool
    source: str | None = None
    data: dict[str, Any] = Field(sa_column=Column(JSON), default_factory=dict)

    article: Article | None = Relationship(back_populates="deposit_warrants")


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

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    article_id: uuid.UUID = Field(foreign_key="article.id", index=True)

    from_status: DepositStatus | None = Field(default=None, index=True)
    to_status: DepositStatus | None = Field(default=None, index=True)
    changed_at: datetime = Field(default_factory=datetime.now, index=True)

    reasons: list[str] = Field(default_factory=list, sa_column=Column(JSON))

    article: Article | None = Relationship(back_populates="deposit_status_transitions")


class AuthorBase(SQLModel):
    """Base SQLModel to define common author attributes.

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

    first_name: str | None = None
    middle_names: str | None = None
    last_name: str | None = None
    full_name: str = Field(index=True)
    canonical_name: str = Field(index=True)
    uw_academic_unit: str | None = Field(default=None, index=True)
    explicitly_searched: bool = Field(default=False, index=True)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class Author(AuthorBase, table=True):
    """SQLModel to store author information."""

    id: int = Field(primary_key=True)

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


class AuthorAffiliationBase(SQLModel):
    """Base SQLModel for author affiliation attributes.

    Attributes
    ----------
    author_id: Foreign key to the Author table.
    year: Year of the UW affiliation.
    """

    author_id: int | None = Field(default=None, foreign_key="author.id")
    year: int = Field(ge=1900)


class AuthorAffiliation(AuthorAffiliationBase, table=True):
    """SQLModel to store author affiliations."""

    id: int = Field(primary_key=True)

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


class AuthorIdentifierBase(SQLModel):
    """Base SQLModel for author identifier attributes.

    Attributes
    ----------
    author_id: Foreign key to the Author table.
    authority: The organization or system that issued the identifier.
    identifier: The actual identifier value.
    created_at: Datetime when the identifier was added.
    """

    author_id: int | None = Field(default=None, foreign_key="author.id")
    authority: str = Field(index=True)
    identifier: str = Field(index=True)
    created_at: datetime = Field(default_factory=datetime.now)


class AuthorIdentifier(AuthorIdentifierBase, table=True):
    """SQLModel to store external identifiers for authors."""

    id: int = Field(primary_key=True)

    author_id: int = Field(
        sa_column=Column(
            ForeignKey("author.id", ondelete="CASCADE"),
        ),
    )

    # Relationships
    author: "Author" = Relationship(back_populates="identifiers")

    __table_args__ = (UniqueConstraint("authority", "identifier", name="uq_author_identifier"),)


class AuthorshipBase(SQLModel):
    """Base SQLModel to define common authorship attributes.

    Attributes
    ----------
    article_id: Foreign key to the Article table.
    author_id: Foreign key to the Author table.
    author_order: Position of author in the publication's author list.
    created_at: Datetime when the relationship was created.
    """

    article_id: uuid.UUID | None = Field(default=None, foreign_key="article.id")
    author_id: int | None = Field(default=None, foreign_key="author.id")
    author_order: int | None = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class Authorship(AuthorshipBase, table=True):
    """SQLModel to store many-to-many relationships between authors and articles."""

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

    # Relationships
    article: "Article" = Relationship(back_populates="authorships")
    author: "Author" = Relationship(back_populates="authorships")
