from __future__ import annotations

from sqlmodel import Field

from open_ire.models import ArticleBase


class ArticleItem(ArticleBase):
    """
    Scrapy data model to include full-text article metadata and related files.
    """

    file_urls: list[str] = Field(default_factory=list)
    files: list[dict[str, str]] | None = None
