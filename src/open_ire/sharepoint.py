import os
from io import BytesIO
from pathlib import Path

from azure.identity import ClientSecretCredential
from msgraph.generated.drives.item.items.item.create_upload_session.create_upload_session_post_request_body import (
    CreateUploadSessionPostRequestBody,
)
from msgraph.generated.models.drive_item import DriveItem
from msgraph.generated.models.drive_item_uploadable_properties import (
    DriveItemUploadableProperties,
)
from msgraph.graph_service_client import GraphServiceClient
from msgraph_core.models import LargeFileUploadSession, UploadResult
from msgraph_core.tasks import LargeFileUploadTask


class SharePoint:
    def __init__(
        self,
        tenant_id: str | None = None,
        client_id: str | None = None,
        site_id: str | None = None,
        client_secret: str | None = None,
    ) -> None:
        self.client_id: str = client_id or os.getenv("SHAREPOINT_CLIENT_ID") or ""
        self.tenant_id: str = tenant_id or os.getenv("SHAREPOINT_TENANT_ID") or ""
        self.site_id: str = site_id or os.getenv("SHAREPOINT_SITE_ID") or ""
        secret: str = client_secret or os.getenv("SHAREPOINT_CLIENT_SECRET") or ""

        if not all((self.client_id, self.tenant_id, self.site_id, secret)):
            msg = (
                "Missing required parameters for SharePoint client. Set the environment variables "
                "SHAREPOINT_CLIENT_ID, SHAREPOINT_TENANT_ID, SHAREPOINT_SITE_ID, and SHAREPOINT_CLIENT_SECRET "
                "or pass them as arguments to the constructor."
            )
            raise ValueError(msg)

        self._drive_id: str | None = None
        self._client = self._authenticate(secret)

    @staticmethod
    def _item_id_from_path(item_path: str) -> str:
        if item_path.startswith("/"):
            item_path = item_path[1:]

        if item_path.endswith("/"):
            item_path = item_path[:-1]

        return f"root:/{item_path}:/"

    def _authenticate(self, client_secret: str) -> GraphServiceClient:
        credential = ClientSecretCredential(
            tenant_id=self.tenant_id,
            client_id=self.client_id,
            client_secret=client_secret,
        )
        scopes = ["https://graph.microsoft.com/.default"]

        return GraphServiceClient(credential, scopes)

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
        file_size = local_file_path.stat().st_size

        upload_body = CreateUploadSessionPostRequestBody(
            item=DriveItemUploadableProperties(
                additional_data={"@microsoft.graph.conflictBehavior": "replace"}
            )
        )
        item_id = self._item_id_from_path(sharepoint_path)
        upload_session = (
            await self._client.drives.by_drive_id(drive_id)
            .items.by_drive_item_id(item_id)
            .create_upload_session.post(upload_body)
        )
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

        with Path(local_file_path).open("rb") as file_content:
            max_chunk_size = min(file_size // 2, 5 * 1024 * 1024)
            task = LargeFileUploadTask(
                large_file_upload_session,
                self._client.request_adapter,
                BytesIO(file_content.read()),
                max_chunk_size=max_chunk_size,
            )
            return await task.upload()

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
