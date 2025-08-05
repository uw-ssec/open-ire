import mimetypes
from pathlib import Path
from typing import Any, Self

from pydantic import ValidationError
from scrapy import Spider
from scrapy.crawler import Crawler
from scrapy.exceptions import DropItem
from scrapy.http import Request, Response
from scrapy.pipelines.files import FilesPipeline
from scrapy.pipelines.media import MediaPipeline
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, create_engine

from open_ire.items import ArticleItem
from open_ire.models import Article, ArticleFile
from open_ire.sharepoint import SharePoint

# Remember to add your pipelines to the `settings.ITEM_PIPELINES` list


class DuplicatesPipeline:
    def __init__(self) -> None:
        self.seen: set[str] = set()

    def process_item(self, item: ArticleItem, spider: Spider) -> ArticleItem:
        if item.reference in self.seen:
            msg = f"Item ID already seen: {item.reference} by {spider.name} spider"
            raise DropItem(msg)
        self.seen.add(item.reference)
        return item


class SQLModelPipeline:
    """
    Persist ArticleItem metadata + downloaded-file info into SQLite via SQLModel.
    """

    def __init__(self, db_path: str) -> None:
        self.db_url = f"sqlite:///{db_path}"
        self.engine = create_engine(self.db_url, connect_args={"check_same_thread": False})

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> Self:
        db_path = crawler.settings.get("OPEN_IRE_DATABASE_FILE")
        if not db_path:
            msg = "OPEN_IRE_DATABASE_FILE must be set in settings.py"
            raise RuntimeError(msg)
        return cls(db_path)

    def open_spider(self, spider: Spider) -> None:  # noqa: ARG002
        SQLModel.metadata.create_all(self.engine)

    def close_spider(self, spider: Spider) -> None:  # noqa: ARG002
        self.engine.dispose()

    def process_item(self, item: ArticleItem, spider: Spider) -> ArticleItem:
        if not item.files:
            msg = f"No files found for article '{item.reference}'."
            raise DropItem(msg)

        valid_files = []
        for i, file_data in enumerate(item.files):
            try:
                if item.store_urls and i < len(item.store_urls) and item.store_urls[i]:
                    file_data["store_url"] = item.store_urls[i]

                file_row = ArticleFile(**file_data)
                valid_files.append(file_row)
            except ValidationError:
                spider.logger.warning("Skipping file due to validation error for article.")

        if not valid_files:
            msg = f"All files for article '{item.reference}' failed validation."
            raise DropItem(msg)

        article_row = Article(**item.model_dump(exclude={"files", "file_urls", "store_urls"}))
        with Session(self.engine) as session:
            try:
                session.add(article_row)
                session.commit()
                session.refresh(article_row)

                for file_row in valid_files:
                    file_row.article_id = article_row.id
                    session.add(file_row)

                session.commit()

            except IntegrityError as e:
                session.rollback()
                msg = "Duplicate item found in database."
                raise DropItem(msg) from e

        return item


class LocalFilePipeline(FilesPipeline):
    def file_path(
        self,
        request: Request,
        response: Response | None = None,
        info: MediaPipeline.SpiderInfo | None = None,
        *,
        item: Any = None,
    ) -> str:
        path: str = super().file_path(request, response, info, item=item)
        if item and hasattr(item, "repository") and item.repository:
            path = path.replace("full/", f"{item.repository}/", 1)

        extension = Path(path).suffix
        if not len(extension) and response:
            content_type_bytes = response.headers.get("Content-Type") or b""
            content_type = content_type_bytes.decode().lower().split(";", 1)[0].strip()
            extension = mimetypes.guess_extension(content_type) or ""

            if extension:
                path = path + extension

        return path


class SharePointPipeline:
    """
    Pipeline to upload files to the SharePoint drive.
    """

    def __init__(self, sharepoint_base_path: str, local_base_path: str) -> None:
        self.sharepoint = SharePoint(base_path=sharepoint_base_path)
        self.base_path = Path(local_base_path)

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> Self:
        if not (local_base_path := crawler.settings.get("FILES_STORE", "")):
            msg = "FILES_STORE must be set in settings.py"
            raise RuntimeError(msg)

        sharepoint_base_path = crawler.settings.get("SHAREPOINT_BASE_PATH", "open_ire")

        return cls(sharepoint_base_path, local_base_path)

    def open_spider(self, spider: Spider) -> None:
        pass

    def close_spider(self, spider: Spider) -> None:
        pass

    async def _save_file(self, file_data: dict[str, str], spider: Spider) -> str:
        sharepoint_path = file_data.get("path", "")
        local_file_path = self.base_path / sharepoint_path

        if not local_file_path.exists():
            msg = f"Local file not found: {local_file_path}"
            spider.logger.error(msg)
            return ""

        store_url = ""
        try:
            msg = f"Uploading file to SharePoint: {local_file_path} -> {sharepoint_path}"
            spider.logger.info(msg)
            upload_result = await self.sharepoint.upload_file(local_file_path, sharepoint_path)
            if upload_result.location:
                drive_item = await self.sharepoint.get_item(sharepoint_path)

                if drive_item and drive_item.web_url:
                    store_url = drive_item.web_url

        except Exception as e:
            msg = f"Error uploading file {local_file_path}: {e}"
            spider.logger.error(msg)

        return store_url

    async def process_item(self, item: ArticleItem, spider: Spider) -> ArticleItem:
        if not item.files:
            msg = f"No files found for article '{item.reference}'."
            spider.logger.warning(msg)
            return item

        store_urls = []
        for file_data in item.files:
            store_urls.append(await self._save_file(file_data, spider))

        item.store_urls = store_urls

        return item
