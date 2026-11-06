"""Store and retrieve SharePoint files through the Microsoft Graph API.

SharePoint moves files in and out of a folder in a SharePoint site's default drive. The
SharePoint pipeline uses it to store downloaded files and database backups off the local machine.
"""

import os
from pathlib import Path
from typing import cast

from azure.identity import ClientSecretCredential
from kiota_http.httpx_request_adapter import HttpxRequestAdapter
from msgraph.generated.drives.item.items.item.create_upload_session.create_upload_session_post_request_body import (
    CreateUploadSessionPostRequestBody,
)
from msgraph.generated.models.drive_item import DriveItem
from msgraph.generated.models.drive_item_uploadable_properties import (
    DriveItemUploadableProperties,
)
from msgraph.graph_service_client import GraphServiceClient
from msgraph_core.middleware.async_graph_transport import AsyncGraphTransport
from msgraph_core.models import LargeFileUploadSession, UploadResult
from msgraph_core.tasks import LargeFileUploadTask


class SharePointClient:
    """Manage files under one folder of a SharePoint site's default drive.

    Paths are relative to `base_path`, which is relative to the drive root.
    Await `close()` when finished to release HTTP connections and credentials.
    """

    _REQUIRED_ENV_VARS = (
        "SHAREPOINT_CLIENT_ID",
        "SHAREPOINT_TENANT_ID",
        "SHAREPOINT_SITE_ID",
        "SHAREPOINT_CLIENT_SECRET",
    )
    # Graph requires upload session chunks to be multiples of 320 KiB.
    _UPLOAD_CHUNK_SIZE = 12 * 320 * 1024

    @staticmethod
    def missing_env_vars() -> list[str]:
        """Return required environment variable names whose values are unset or empty."""
        return [name for name in SharePointClient._REQUIRED_ENV_VARS if not os.getenv(name)]

    def __init__(
        self,
        base_path: str,
        tenant_id: str | None = None,
        client_id: str | None = None,
        site_id: str | None = None,
        client_secret: str | None = None,
    ) -> None:
        """Create a client with explicit credentials or environment variable defaults.

        Each credential argument falls back to its environment variable when
        omitted or empty. Authentication and drive lookup occur on first use.

        Args:
            base_path: Base folder relative to the site's default drive root,
                without leading or trailing slashes.
            tenant_id: Microsoft Entra tenant ID; defaults to `SHAREPOINT_TENANT_ID`.
            client_id: Application client ID; defaults to `SHAREPOINT_CLIENT_ID`.
            site_id: Graph site ID; defaults to `SHAREPOINT_SITE_ID`.
            client_secret: Application secret; defaults to `SHAREPOINT_CLIENT_SECRET`.

        Raises:
            ValueError: If any credential is missing from both the arguments and the environment.
        """
        self.base_path: str = base_path
        self.client_id: str = client_id or os.getenv("SHAREPOINT_CLIENT_ID") or ""
        self.tenant_id: str = tenant_id or os.getenv("SHAREPOINT_TENANT_ID") or ""
        self.site_id: str = site_id or os.getenv("SHAREPOINT_SITE_ID") or ""
        secret: str = client_secret or os.getenv("SHAREPOINT_CLIENT_SECRET") or ""

        if not all((self.client_id, self.tenant_id, self.site_id, secret)):
            msg = (
                "Missing required parameters for SharePoint client. Set the environment variables "
                "SHAREPOINT_CLIENT_ID, SHAREPOINT_TENANT_ID, SHAREPOINT_SITE_ID, and SHAREPOINT_CLIENT_SECRET "
                "or pass them as arguments to the client constructor."
            )
            raise ValueError(msg)

        self._drive_id: str | None = None
        self._client = self._create_client(secret)

    def _create_client(self, client_secret: str) -> GraphServiceClient:
        """Build the Graph client using the application credentials.

        No network call is made, so invalid credentials go unnoticed until the
        first Graph request.
        """
        # Keep the credentials so that we can release them when `close` is called.
        self._credential = ClientSecretCredential(
            tenant_id=self.tenant_id,
            client_id=self.client_id,
            client_secret=client_secret,
        )
        scopes = ["https://graph.microsoft.com/.default"]

        return GraphServiceClient(self._credential, scopes)

    def _item_id_from_path(self, item_path: str) -> str:
        """Format a path relative to `base_path` as a Graph `root:/path:/` item ID."""
        item_path = item_path.strip("/")
        if not item_path:
            return f"root:/{self.base_path}:/"

        return f"root:/{self.base_path}/{item_path}:/"

    async def _get_drive_id(self) -> str:
        """Return the cached default drive ID, fetching it on first use.

        Raises:
            RuntimeError: If Graph returns no drive or no drive ID.
        """
        if not self._drive_id:
            drive = await self._client.sites.by_site_id(self.site_id).drive.get()
            self._drive_id = drive.id if drive else None

        if self._drive_id is None:
            msg = "Failed to get default drive for SharePoint site"
            raise RuntimeError(msg)

        return self._drive_id

    async def upload_file(self, local_file_path: Path, item_path: str) -> UploadResult:
        """Upload a file, replacing any file at the destination path.

        Files no larger than one upload chunk, including empty files, are read
        into memory and sent in one request. Larger files stream from disk
        through an upload session.

        Args:
            local_file_path: Path to the local file to upload.
            item_path: Destination path relative to `base_path`, including the filename.

        Returns:
            Upload result. For a single-request upload, `location` is the returned
            item's web URL, if available. For a session upload, it is the upload
            session URL; use `get_item()` to retrieve the file's web URL.

        Raises:
            FileNotFoundError: If the local file does not exist.
            RuntimeError: If Graph returns no default drive ID or no upload session.
        """
        if not local_file_path.exists():
            msg = f"Local file not found: {local_file_path}"
            raise FileNotFoundError(msg)

        drive_id = await self._get_drive_id()
        item_request = self._client.drives.by_drive_id(drive_id).items.by_drive_item_id(
            self._item_id_from_path(item_path)
        )

        # LargeFileUploadTask can't upload an empty file, and it resends the final chunk
        # malformed when the whole file fits in one, so those files get a simple upload.
        if local_file_path.stat().st_size <= self._UPLOAD_CHUNK_SIZE:
            drive_item = await item_request.content.put(local_file_path.read_bytes())
            result: UploadResult = UploadResult()
            result.item_response = drive_item
            result.location = drive_item.web_url if drive_item else None
            return result

        upload_body = CreateUploadSessionPostRequestBody(
            item=DriveItemUploadableProperties(
                additional_data={"@microsoft.graph.conflictBehavior": "replace"}
            )
        )
        upload_session = await item_request.create_upload_session.post(upload_body)
        if not upload_session:
            msg = f"Failed to create upload session for {local_file_path}"
            raise RuntimeError(msg)

        large_file_upload_session = LargeFileUploadSession(
            upload_url=upload_session.upload_url,
            expiration_date_time=upload_session.expiration_date_time,
            additional_data=upload_session.additional_data,
            is_cancelled=False,
            next_expected_ranges=upload_session.next_expected_ranges,
        )

        # The task is typed for BytesIO but reads any seekable file, getting the size from
        # its name, so the file streams from disk instead of being held in memory.
        with local_file_path.open("rb") as file_stream:
            task = LargeFileUploadTask(
                large_file_upload_session,
                self._client.request_adapter,
                file_stream,
                max_chunk_size=self._UPLOAD_CHUNK_SIZE,
            )
            return await task.upload()

    async def download_file(self, sharepoint_path: str, local_file_path: Path) -> None:
        """Download a file, replacing any local file at the destination path.

        The whole response is held in memory before writing it. Missing local
        parent folders are created, and an empty response produces an empty file.

        Args:
            sharepoint_path: Path to the file, relative to `base_path`.
            local_file_path: Local destination path for the downloaded file.

        Raises:
            RuntimeError: If Graph returns no default drive ID.
        """
        drive_id = await self._get_drive_id()
        item_id = self._item_id_from_path(sharepoint_path)
        content = (
            await self._client.drives.by_drive_id(drive_id)
            .items.by_drive_item_id(item_id)
            .content.get()
        )

        # Kiota raises on HTTP errors and returns None for an empty body.
        local_file_path.parent.mkdir(parents=True, exist_ok=True)
        with local_file_path.open("wb") as local_file:
            local_file.write(content or b"")

    async def delete_item(self, item_path: str) -> bool:
        """Delete a file or a folder and its contents from SharePoint.

        Args:
            item_path: Path to the item to delete, relative to `base_path`.

        Returns:
            True if deletion was successful; a failed deletion raises instead.

        Raises:
            RuntimeError: If Graph returns no default drive ID.
        """
        drive_id = await self._get_drive_id()
        item_id = self._item_id_from_path(item_path)

        await self._client.drives.by_drive_id(drive_id).items.by_drive_item_id(item_id).delete()

        return True

    async def get_item(self, item_path: str) -> DriveItem | None:
        """Retrieve metadata for a file or folder in SharePoint.

        Args:
            item_path: Path to the item, relative to `base_path`.

        Returns:
            Item metadata, or None if Graph sends an empty response.

        Raises:
            ODataError: If Graph reports an error, including an item that does not exist.
            RuntimeError: If Graph returns no default drive ID.
        """
        drive_id = await self._get_drive_id()
        item_id = self._item_id_from_path(item_path)

        return await self._client.drives.by_drive_id(drive_id).items.by_drive_item_id(item_id).get()

    async def list_children(self, folder_path: str) -> list[DriveItem]:
        """List the items directly inside a folder.

        Graph returns children a page at a time, so this follows
        `@odata.nextLink` until every page has been read.

        Args:
            folder_path: Path to the folder, relative to `base_path`. Pass an
                empty string for `base_path` itself.

        Returns:
            The folder's child items, or an empty list if it has none.
        """
        drive_id = await self._get_drive_id()
        item_id = self._item_id_from_path(folder_path)
        children = (
            self._client.drives.by_drive_id(drive_id).items.by_drive_item_id(item_id).children
        )

        items: list[DriveItem] = []
        response = await children.get()
        while response is not None:
            items.extend(response.value or [])
            if not response.odata_next_link:
                break
            response = await children.with_url(response.odata_next_link).get()

        return items

    async def close(self) -> None:
        """Release HTTP connections and credentials when finished with the client.

        The client cannot be reused after closing.
        """
        http_client = cast(HttpxRequestAdapter, self._client.request_adapter)._http_client
        # GraphServiceClient has no close method, and AsyncGraphTransport doesn't forward aclose,
        # so closing http_client alone would leave the sockets open.
        for transport in (http_client._transport, *http_client._mounts.values()):
            if isinstance(transport, AsyncGraphTransport):
                await transport.transport.aclose()
        await http_client.aclose()
        self._credential.close()
