from pydantic import BaseModel
from sqlmodel import Field, SQLModel

from open_ire.models import ArticleBase


class ArticleItem(ArticleBase):
    """
    Scrapy data model to include full-text article metadata and related files.
    """

    file_reference_urls: list[tuple[str, str]] = Field(default_factory=list)
    file_references: list[dict[str, str | int | None]] | None = None
    file_urls: list[str] = Field(default_factory=list)
    files: list[dict[str, str | int | None]] | None = None
    store_urls: list[str] = Field(default_factory=list)


class UnavailableArticleItem(SQLModel):
    """
    Scrapy item for previously collected articles no longer available at source URL.
    """

    article_id: str
    repository: str
    reference: str
    url: str
    status_code: int | None = None
    error: str
    request_method: str
    checked_at: str


class AuthorItem(BaseModel):
    """Author data with identifiers from a trusted source (e.g., OpenAlex).

    Used when a spider successfully disambiguates an author and discovers
    their canonical identifiers (OpenAlex ID, ORCID, etc.).
    """

    full_name: str
    first_name: str | None = None
    middle_names: str | None = None
    last_name: str | None = None
    identifiers: list[dict[str, str]] = Field(default_factory=list)
