import logging
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from open_ire.errors import DatabaseDuplicateItemError
from open_ire.items import ArticleItem
from open_ire.models import Article, ArticleFile, ArticleFileReference
from open_ire.pipelines.base_sql_model_pipeline import BaseSQLModelPipeline

logger = logging.getLogger(__name__)


class SQLModelPipeline(BaseSQLModelPipeline):
    """
    Persist ArticleItem metadata + downloaded-file info into SQLite via SQLModel.
    """

    @staticmethod
    def _get_article_file_references(item: ArticleItem) -> list[ArticleFileReference]:
        article_file_refs = []
        file_references = item.file_references or []

        for file_ref in file_references:
            try:
                article_file_refs.append(ArticleFileReference(**file_ref))
            except ValidationError:
                logger.warning("Skipping file reference due to validation error.")

        return article_file_refs

    @staticmethod
    def _save_article_files(
        session: Session,
        article_id: Any,
        article_files: list[ArticleFile] | list[ArticleFileReference],
    ) -> None:
        for file_row in article_files:
            file_row.article_id = article_id
            try:
                session.add(file_row)
                session.flush()
            except IntegrityError as e:
                msg = f"Integrity error while saving file for article '{article_id}: {e}"
                session.rollback()
                logger.warning(msg)

    def _get_file_size(self, file_path: Path) -> int | None:
        full_path = Path(self.files_base_path or "") / file_path
        try:
            if full_path.exists() and full_path.is_file():
                return full_path.stat().st_size
        except OSError:
            pass

        return None

    def _get_article_files(self, item: ArticleItem) -> list[ArticleFile]:
        article_files = []
        files = item.files or []

        for i, file_data in enumerate(files):
            try:
                file_path = Path(str(file_data.get("path") or ""))
                file_data["extension"] = file_path.suffix.lstrip(".")
                file_data["size"] = self._get_file_size(file_path)
                file_data["store_url"] = (
                    item.store_urls[i] if item.store_urls and i < len(item.store_urls) else None
                )
                file_row = ArticleFile(**file_data)
                article_files.append(file_row)
            except ValidationError:
                logger.warning("Skipping file due to validation error.")

        return article_files

    def _update_existing_article(
        self,
        session: Session,
        existing_article: Article,
        item_data: dict[str, Any],
        article_files: list[ArticleFile],
        file_references: list[ArticleFileReference],
    ) -> None:
        for key, value in item_data.items():
            if key not in ("id", "created_at"):
                setattr(existing_article, key, value)

        session.commit()
        session.refresh(existing_article)

        self._save_article_files(session, existing_article.id, article_files)
        self._save_article_files(session, existing_article.id, file_references)

        session.commit()

    def _create_new_article(
        self,
        session: Session,
        item_data: dict[str, Any],
        article_files: list[ArticleFile],
        file_references: list[ArticleFileReference],
    ) -> None:
        article = Article(**item_data)

        try:
            session.add(article)
            session.commit()
            session.refresh(article)

            self._save_article_files(session, article.id, article_files)
            self._save_article_files(session, article.id, file_references)

            session.commit()

        except IntegrityError as e:
            session.rollback()
            raise DatabaseDuplicateItemError() from e

    def process_item(self, item: ArticleItem) -> ArticleItem:
        article_files = self._get_article_files(item)
        file_references = self._get_article_file_references(item)
        item_data = item.model_dump(
            exclude={
                "file_reference_urls",
                "file_references",
                "file_urls",
                "files",
                "store_urls",
            }
        )

        with Session(self.engine) as session:
            if existing_article := self.find_existing_article(session, item):
                self._update_existing_article(
                    session,
                    existing_article,
                    item_data,
                    article_files,
                    file_references,
                )
            else:
                self._create_new_article(session, item_data, article_files, file_references)

        return item
