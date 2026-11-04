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


class SharePoint:
    _REQUIRED_ENV_VARS = (
        "SHAREPOINT_CLIENT_ID",
        "SHAREPOINT_TENANT_ID",
        "SHAREPOINT_SITE_ID",
        "SHAREPOINT_CLIENT_SECRET",
    )
    # Graph requires upload session chunks to be multiples of 320 KiB.
    _UPLOAD_CHUNK_SIZE = 12 * 320 * 1024

    @staticmethod
    def missing_credentials() -> list[str]:
        """Return the names of required SharePoint environment variables that are unset or empty."""
        return [name for name in SharePoint._REQUIRED_ENV_VARS if not os.getenv(name)]

    def __init__(
        self,
        base_path: str,
        tenant_id: str | None = None,
        client_id: str | None = None,
        site_id: str | None = None,
        client_secret: str | None = None,
    ) -> None:
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
        self._client = self._authenticate(secret)

    def _authenticate(self, client_secret: str) -> GraphServiceClient:
        self._credential = ClientSecretCredential(
            tenant_id=self.tenant_id,
            client_id=self.client_id,
            client_secret=client_secret,
        )
        scopes = ["https://graph.microsoft.com/.default"]

        return GraphServiceClient(self._credential, scopes)

    def _item_id_from_path(self, item_path: str) -> str:
        item_path = item_path.removeprefix("/")
        item_path = item_path.removesuffix("/")

        return f"root:/{self.base_path}/{item_path}:/"

    async def _get_drive_id(self) -> str:
        """Get the default drive for the SharePoint site."""
        if not self._drive_id:
            drive = await self._client.sites.by_site_id(self.site_id).drive.get()
            self._drive_id = drive.id if drive else None

        if self._drive_id is None:
            msg = "Failed to get default drive for SharePoint site"
            raise RuntimeError(msg)

        return self._drive_id

    async def upload_file(self, local_file_path: Path, sharepoint_path: str) -> UploadResult:
        """
        Upload a file to SharePoint Drive.

        Args:
            local_file_path: Path to the local file to upload.
            sharepoint_path: Destination path in SharePoint (including filename).

        Returns:
            Result of uploading the drive item.
        """
        if not local_file_path.exists():
            msg = f"Local file not found: {local_file_path}"
            raise FileNotFoundError(msg)

        drive_id = await self._get_drive_id()
        item = self._client.drives.by_drive_id(drive_id).items.by_drive_item_id(
            self._item_id_from_path(sharepoint_path)
        )

        # LargeFileUploadTask can't upload an empty file, and it resends the final chunk
        # malformed when the whole file fits in one, so those files get a simple upload.
        if local_file_path.stat().st_size <= self._UPLOAD_CHUNK_SIZE:
            drive_item = await item.content.put(local_file_path.read_bytes())
            result: UploadResult = UploadResult()
            result.item_response = drive_item
            result.location = drive_item.web_url if drive_item else None
            return result

        upload_body = CreateUploadSessionPostRequestBody(
            item=DriveItemUploadableProperties(
                additional_data={"@microsoft.graph.conflictBehavior": "replace"}
            )
        )
        upload_session = await item.create_upload_session.post(upload_body)
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
        """
        Download a file from SharePoint Drive.

        Args:
            sharepoint_path: Path to the file in SharePoint.
            local_file_path: Local destination path for the downloaded file.
        """
        drive_id = await self._get_drive_id()
        item_id = self._item_id_from_path(sharepoint_path)
        stream = (
            await self._client.drives.by_drive_id(drive_id)
            .items.by_drive_item_id(item_id)
            .content.get()
        )

        # Kiota raises on HTTP errors and returns None for an empty body.
        local_file_path.parent.mkdir(parents=True, exist_ok=True)
        with local_file_path.open("wb") as local_file:
            local_file.write(stream or b"")

    async def delete_item(self, item_path: str) -> bool:
        """
        Delete a file from SharePoint Drive.

        Args:
            item_path: Path to the item to delete.

        Returns:
            True if deletion was successful.
        """
        drive_id = await self._get_drive_id()
        item_id = self._item_id_from_path(item_path)

        await self._client.drives.by_drive_id(drive_id).items.by_drive_item_id(item_id).delete()

        return True

    async def get_item(self, item_path: str) -> DriveItem | None:
        """
        Get information about a specific item in SharePoint Drive.

        Args:
            item_path: Path to the item

        Returns:
            DriveItem containing file metadata.
        """
        drive_id = await self._get_drive_id()
        item_id = self._item_id_from_path(item_path)

        return await self._client.drives.by_drive_id(drive_id).items.by_drive_item_id(item_id).get()

    async def close(self) -> None:
        """Close the connections to Microsoft; the client can't be used afterward."""
        http_client = cast(HttpxRequestAdapter, self._client.request_adapter)._http_client
        # GraphServiceClient has no close method, and AsyncGraphTransport doesn't forward aclose,
        # so closing http_client alone would leave the sockets open.
        for transport in (http_client._transport, *http_client._mounts.values()):
            if isinstance(transport, AsyncGraphTransport):
                await transport.transport.aclose()
        await http_client.aclose()
        self._credential.close()
