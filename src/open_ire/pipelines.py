import logging
import mimetypes
from datetime import datetime
from pathlib import Path
from typing import Any, Self
from urllib.parse import unquote, urlparse

from itemadapter import ItemAdapter
from pydantic import ValidationError
from requests import utils as requests_utils
from scrapy import Spider
from scrapy.crawler import Crawler
from scrapy.http import Request, Response
from scrapy.http.request import NO_CALLBACK
from scrapy.pipelines.files import FilesPipeline
from scrapy.pipelines.media import MediaPipeline
from scrapy.utils.defer import maybe_deferred_to_future
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, create_engine, select

from open_ire.errors import (
    ConfigurationError,
    DatabaseDuplicateItemError,
    DuplicateItemError,
)
from open_ire.items import ArticleItem, OAPPublicationItem
from open_ire.models import Article, ArticleFile, ArticleFileReference, OAPPublication
from open_ire.sharepoint import SharePoint

logger = logging.getLogger(__name__)

# Remember to add your pipelines to the `settings.ITEM_PIPELINES` list


class DuplicatesPipeline:
    """
    Drops duplicate items for a given spider using the `reference` field.
    """

    def __init__(self) -> None:
        self.seen: set[str] = set()

    def process_item(self, item: ArticleItem, spider: Spider) -> ArticleItem:
        if item.reference in self.seen:
            raise DuplicateItemError(item.reference, spider.name)

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
            conf = "OPEN_IRE_DATABASE_FILE"
            raise ConfigurationError(conf)

        parent_dir = Path(db_path).parent
        if not parent_dir.exists():
            parent_dir.mkdir(parents=True, exist_ok=True)
            logger.info("Created OPEN_IRE database directory at %s", parent_dir)

        if not (files_base_path := crawler.settings.get("FILES_STORE", "")):
            conf = "FILES_STORE"
            raise ConfigurationError(conf)

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

    @staticmethod
    def _find_existing_article(session: Session, item: ArticleItem) -> Article | None:
        return session.exec(
            select(Article).where(
                Article.repository == item.repository,
                Article.reference == item.reference,
            )
        ).first()

    @staticmethod
    def _save_article_files(
        spider: Spider,
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
                spider.logger.warning(msg)

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

    def _update_existing_article(
        self,
        spider: Spider,
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

        self._save_article_files(spider, session, existing_article.id, article_files)
        self._save_article_files(spider, session, existing_article.id, file_references)

        session.commit()

    def _create_new_article(
        self,
        spider: Spider,
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

            self._save_article_files(spider, session, article.id, article_files)
            self._save_article_files(spider, session, article.id, file_references)

            session.commit()

        except IntegrityError as e:
            session.rollback()
            raise DatabaseDuplicateItemError() from e

    def process_item(self, item: ArticleItem, spider: Spider) -> ArticleItem:
        article_files = self._get_article_files(item, spider)
        file_references = self._get_article_file_references(item, spider)
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
            if existing_article := self._find_existing_article(session, item):
                self._update_existing_article(
                    spider,
                    session,
                    existing_article,
                    item_data,
                    article_files,
                    file_references,
                )
            else:
                self._create_new_article(spider, session, item_data, article_files, file_references)

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

    @staticmethod
    def _extract_file_extension(response: Response) -> str:
        content_disposition = (response.headers.get("Content-Disposition") or b"").decode()
        if content_disposition:
            filename = LocalFilePipeline._extract_filename_from_content_disposition(
                content_disposition
            )
            if filename and (extension := Path(filename).suffix):
                return extension.lower()

        if content_type_bytes := response.headers.get("Content-Type", b""):
            return LocalFilePipeline._extract_extension_from_content_type(
                content_type_bytes.decode()
            )

        return ""

    def get_media_requests(self, item: Any, info: MediaPipeline.SpiderInfo) -> list[Request]:  # noqa: ARG002
        urls = ItemAdapter(item).get(self.files_urls_field, [])
        return [
            Request(u, headers=requests_utils.default_headers(), callback=NO_CALLBACK) for u in urls
        ]

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
        self, spider: Spider, source_url: str, reference_url: str
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
            file_reference = await self._get_file_reference(spider, source_url, ref_url)
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
            conf = "FILES_STORE"
            raise ConfigurationError(conf)

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


class OAPPublicationSQLModelPipeline:
    """Persist OAP publication items into the configured SQLite database."""

    def __init__(self, db_path: str) -> None:
        self.db_url = f"sqlite:///{db_path}"
        self.engine = create_engine(self.db_url)

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> Self:
        db_path = crawler.settings.get("OPEN_IRE_DATABASE_FILE")
        if not db_path:
            conf = "OPEN_IRE_DATABASE_FILE"
            raise ConfigurationError(conf)

        parent_dir = Path(db_path).parent
        if not parent_dir.exists():
            parent_dir.mkdir(parents=True, exist_ok=True)
            logger.info("Created OPEN_IRE database directory at %s", parent_dir)

        return cls(db_path)

    @staticmethod
    def _find_existing(session: Session, item: OAPPublicationItem) -> OAPPublication | None:
        statement = select(OAPPublication).where(
            OAPPublication.repository == item.repository,
            OAPPublication.external_id == item.external_id,
        )
        return session.exec(statement).first()

    @staticmethod
    def _update_existing(
        record: OAPPublication,
        item_data: dict[str, Any],
        session: Session,
    ) -> None:
        for key, value in item_data.items():
            if key not in {"id", "created_at"}:
                setattr(record, key, value)

        record.updated_at = datetime.now()
        session.add(record)

    @staticmethod
    def _create_new(session: Session, item_data: dict[str, Any]) -> None:
        session.add(OAPPublication(**item_data))

    def open_spider(self, spider: Spider) -> None:  # noqa: ARG002
        SQLModel.metadata.create_all(self.engine)

    def close_spider(self, spider: Spider) -> None:  # noqa: ARG002
        self.engine.dispose()

    def process_item(self, item: Any, spider: Spider) -> Any:
        if not isinstance(item, OAPPublicationItem):
            return item

        item.updated_at = datetime.now()
        item_data = item.model_dump()

        with Session(self.engine) as session:
            try:
                if existing := self._find_existing(session, item):
                    self._update_existing(existing, item_data, session)
                else:
                    self._create_new(session, item_data)

                session.commit()
            except IntegrityError:
                session.rollback()
                spider.logger.warning(
                    "Duplicate OAP publication skipped: %s (%s)", item.external_id, item.repository
                )

        return item
