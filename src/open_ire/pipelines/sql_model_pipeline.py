import logging
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from open_ire.errors import DatabaseDuplicateItemError
from open_ire.items import ArticleItem
from open_ire.models import Article, ArticleFile
from open_ire.pipelines.base_sql_model_pipeline import BaseSQLModelPipeline

logger = logging.getLogger(__name__)


class SQLModelPipeline(BaseSQLModelPipeline):
    """
    Persist ArticleItem metadata + downloaded-file info into SQLite via SQLModel.
    """

    @staticmethod
    def _save_article_files(
        session: Session,
        article_id: Any,
        article_files: list[ArticleFile],
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
                file_row = ArticleFile.model_validate(file_data)
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
    ) -> None:
        for key, value in item_data.items():
            if key not in ("id", "created_at"):
                setattr(existing_article, key, value)

        session.commit()
        session.refresh(existing_article)

        self._save_article_files(session, existing_article.id, article_files)

        session.commit()

    def _create_new_article(
        self,
        session: Session,
        item_data: dict[str, Any],
        article_files: list[ArticleFile],
    ) -> None:
        article = Article(**item_data)

        try:
            session.add(article)
            session.commit()
            session.refresh(article)

            self._save_article_files(session, article.id, article_files)

            session.commit()

        except IntegrityError as e:
            session.rollback()
            raise DatabaseDuplicateItemError() from e

    def process_item(self, item: Any) -> Any:
        if not isinstance(item, ArticleItem):
            return item

        article_files = self._get_article_files(item)
        item_data = item.model_dump(
            exclude={
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
                )
            else:
                self._create_new_article(session, item_data, article_files)

        return item
