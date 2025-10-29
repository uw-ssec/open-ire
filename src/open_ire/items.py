from sqlmodel import Field

from open_ire.models import ArticleBase, OAPublicationBase


class ArticleItem(ArticleBase):
    """
    Scrapy data model to include full-text article metadata and related files.
    """

    file_reference_urls: list[tuple[str, str]] = Field(default_factory=list)
    file_references: list[dict[str, str | int | None]] | None = None
    file_urls: list[str] = Field(default_factory=list)
    files: list[dict[str, str | int | None]] | None = None
    store_urls: list[str] = Field(default_factory=list)


class OAPublicationItem(OAPublicationBase):
    """
    Scrapy data model for OAP metadata.
    """
