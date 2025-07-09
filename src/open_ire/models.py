import uuid
from datetime import date
from typing import Any

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, Relationship, SQLModel


class ArticleBase(SQLModel):
    """Base SQLModel to define common article attributes.

    Attributes
    ----------
    abstract:
        Summary or abstract of the resource, if available.
    authors:
        Names of the authors or creators of the resource.
    doi:
        Digital Object Identifier, if assigned.
    eissn:
        Electronic International Standard Serial Number, if available.
    isbn:
        International Standard Book Number, if assigned.
    issn:
        International Standard Serial Number, if assigned.
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
    """

    abstract: str | None = None
    authors: str | None = None
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

    __table_args__ = (
        UniqueConstraint("repository", "reference", name="uq_article_repository_reference"),
    )


class ArticleFile(SQLModel, table=True):
    """SQLModel to store files associated with articles.

    Attributes
    ----------
    id:
        Primary key for the database.
    article_id:
        Foreign key to the Article table.
    url:
        URL of the file.
    path:
        Local path where the file is stored.
    checksum:
        Checksum of the file.
    """

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    url: str
    path: str
    checksum: str

    article_id: uuid.UUID | None = Field(default=None, foreign_key="article.id")
    article: Article | None = Relationship(back_populates="files")
