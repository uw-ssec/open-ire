import mimetypes
from pathlib import Path
from typing import Any, Self
from urllib.parse import unquote, urlparse

from pydantic import ValidationError
from scrapy import Spider
from scrapy.crawler import Crawler
from scrapy.exceptions import DropItem
from scrapy.http import Request, Response
from scrapy.http.request import NO_CALLBACK
from scrapy.pipelines.files import FilesPipeline
from scrapy.pipelines.media import MediaPipeline
from scrapy.utils.defer import maybe_deferred_to_future
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, create_engine

from open_ire.items import ArticleItem
from open_ire.models import Article, ArticleFile, ArticleFileReference
from open_ire.sharepoint import SharePoint

# Remember to add your pipelines to the `settings.ITEM_PIPELINES` list


class DuplicatesPipeline:
    """
    Drops duplicate items for a given spider using the `reference` field.
    """

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

    def __init__(self, db_path: str, files_base_path: str) -> None:
        self.files_base_path = files_base_path
        self.db_url = f"sqlite:///{db_path}"
        self.engine = create_engine(self.db_url, connect_args={"check_same_thread": False})

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> Self:
        db_path = crawler.settings.get("OPEN_IRE_DATABASE_FILE")
        if not db_path:
            msg = "OPEN_IRE_DATABASE_FILE must be set in settings.py"
            raise RuntimeError(msg)

        if not (files_base_path := crawler.settings.get("FILES_STORE", "")):
            msg = "FILES_STORE must be set in settings.py"
            raise RuntimeError(msg)

        return cls(db_path, files_base_path)

    @staticmethod
    def _get_article_file_references(
        item: ArticleItem, spider: Spider
    ) -> list[ArticleFileReference]:
        article_file_refs = []
        file_references = item.file_references or []

        for file_ref in file_references:
            try:
                article_file_refs.append(ArticleFileReference(**file_ref))
            except ValidationError:
                spider.logger.warning("Skipping file reference due to validation error.")

        return article_file_refs

    def _get_file_size(self, file_path: Path) -> int | None:
        full_path = Path(self.files_base_path) / file_path
        try:
            if full_path.exists() and full_path.is_file():
                return full_path.stat().st_size
        except OSError:
            pass

        return None

    def _get_article_files(self, item: ArticleItem, spider: Spider) -> list[ArticleFile]:
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
                spider.logger.warning("Skipping file due to validation error.")

        return article_files

    def open_spider(self, spider: Spider) -> None:  # noqa: ARG002
        SQLModel.metadata.create_all(self.engine)

    def close_spider(self, spider: Spider) -> None:  # noqa: ARG002
        self.engine.dispose()

    def process_item(self, item: ArticleItem, spider: Spider) -> ArticleItem:
        article_files = self._get_article_files(item, spider)
        file_references = self._get_article_file_references(item, spider)
        article = Article(
            **item.model_dump(
                exclude={
                    "file_reference_urls",
                    "file_references",
                    "file_urls",
                    "files",
                    "store_urls",
                }
            )
        )
        with Session(self.engine) as session:
            try:
                session.add(article)
                session.commit()
                session.refresh(article)

                for file_row in article_files:
                    file_row.article_id = article.id
                    session.add(file_row)

                for file_ref in file_references:
                    file_ref.article_id = article.id
                    session.add(file_ref)

                session.commit()

            except IntegrityError as e:
                session.rollback()
                msg = "Duplicate item found in database."
                raise DropItem(msg) from e

        return item


class LocalFilePipeline(FilesPipeline):
    """
    Stores files in the local filesystem, using the `repository` field as the subdirectory.
    """

    @staticmethod
    def _extract_filename_from_content_disposition(
        content_disposition: str,
    ) -> str | None:
        if not content_disposition:
            return None

        for part in (p.strip() for p in content_disposition.split(";")):
            if part.lower().startswith("filename="):
                return part[9:].strip("\"'")
            if part.lower().startswith("filename*=") and "''" in part:
                # RFC 5987
                filename_part = part[10:].strip().split("''", 1)[-1]
                return unquote(filename_part)

        return None

    @staticmethod
    def _extract_extension_from_content_type(content_type: str) -> str:
        clean_content_type = content_type.lower().split(";", 1)[0].strip()
        if extension := mimetypes.guess_extension(clean_content_type):
            return extension.lower()

        return ""

    def _extract_file_extension(self, response: Response) -> str:
        content_disposition = (response.headers.get("Content-Disposition") or b"").decode()
        if content_disposition:
            filename = self._extract_filename_from_content_disposition(content_disposition)
            if filename and (extension := Path(filename).suffix):
                return extension.lower()

        if content_type_bytes := response.headers.get("Content-Type", b""):
            return self._extract_extension_from_content_type(content_type_bytes.decode())

        return ""

    def file_path(
        self,
        request: Request,
        response: Response | None = None,
        info: MediaPipeline.SpiderInfo | None = None,
        *,
        item: Any = None,
    ) -> str:
        path = super().file_path(request, response, info, item=item)

        if item and getattr(item, "repository", None):
            path = path.replace("full/", f"{item.repository}/", 1)

        if (
            response
            and not Path(path).suffix
            and (extension := self._extract_file_extension(response))
        ):
            path += extension

        return path


class FileReferencePipeline:
    """
    Populates the `file_references` field of `ArticleItem` entities with metadata for external files.
    """

    @staticmethod
    def _extract_file_size(response: Response) -> int | None:
        content_length = response.headers.get("Content-Length")
        if content_length:
            try:
                return int(content_length.decode())
            except (ValueError, AttributeError):
                pass

        return None

    @staticmethod
    def _extract_extension(url: str) -> str | None:
        parsed = urlparse(url)
        if path := parsed.path:
            extension = Path(path).suffix.lstrip(".")
            return extension.lower() if extension else None

        return None

    async def _get_file_reference(
        self, source_url: str, reference_url: str, spider: Spider
    ) -> dict[str, str | int | None]:
        file_reference: dict[str, str | int | None] = {
            "extension": self._extract_extension(reference_url),
            "size": None,
            "source_url": source_url,
            "url": reference_url,
        }

        request = Request(reference_url, method="HEAD", callback=NO_CALLBACK)
        if not spider.crawler.engine:
            return file_reference

        response = await maybe_deferred_to_future(spider.crawler.engine.download(request))

        if response.status != 200:
            return file_reference

        file_reference["size"] = self._extract_file_size(response)

        return file_reference

    async def process_item(self, item: ArticleItem, spider: Spider) -> ArticleItem:
        if not item.file_reference_urls:
            return item

        file_references = []
        for source_url, ref_url in item.file_reference_urls:
            file_reference = await self._get_file_reference(source_url, ref_url, spider)
            file_references.append(file_reference)

        item.file_references = file_references

        return item


class SharePointPipeline:
    """
    Uploads files to a SharePoint drive, if configured.
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

    async def _save_file(self, file_data: dict[str, str | int | None], spider: Spider) -> str:
        sharepoint_path = str(file_data.get("path") or "")
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
