import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from itemadapter import ItemAdapter
from requests import utils as requests_utils
from scrapy.http import Request, Response
from scrapy.http.request import NO_CALLBACK
from scrapy.pipelines.files import FilesPipeline
from scrapy.pipelines.media import MediaPipeline


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
