import os
from enum import Enum
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from msgraph.generated.models.drive import Drive
from msgraph.generated.models.drive_item import DriveItem
from msgraph_core.models import UploadResult

from open_ire.sharepoint import SharePoint


class DefaultValues(Enum):
    BASE_PATH = "test-base-path"
    CLIENT_ID = "test-client-id"
    CLIENT_SECRET = "test-client-secret"
    DRIVE_ID = "test-drive-id"
    DRIVE_ITEM_ID = "test-drive-item-id"
    DRIVE_ITEM_NAME = "test.txt"
    SITE_ID = "test-site-id"
    TENANT_ID = "test-tenant-id"


class TestSharePoint:
    @staticmethod
    def _mock_drive(drive_id: str = DefaultValues.DRIVE_ID.value) -> MagicMock:
        mock_drive = MagicMock(spec=Drive)
        mock_drive.id = drive_id

        return mock_drive

    @staticmethod
    def _mock_drive_item(name: str = "test.txt", size: int = 1024) -> MagicMock:
        mock_drive_item = MagicMock(spec=DriveItem)
        mock_drive_item.name = name
        mock_drive_item.size = size

        return mock_drive_item

    @staticmethod
    def _mock_upload_session() -> MagicMock:
        mock_upload_session = MagicMock()
        mock_upload_session.upload_url = "https://unittest.url"
        mock_upload_session.expiration_date_time = "2026-01-01T00:00:00Z"
        mock_upload_session.additional_data = {}
        mock_upload_session.next_expected_ranges = []

        return mock_upload_session

    @staticmethod
    def _mock_client_drive_id(
        sharepoint_client, drive_id: str = DefaultValues.DRIVE_ID.value
    ):
        sharepoint_client._get_drive_id = AsyncMock(return_value=drive_id)

    @staticmethod
    def _mock_client_drive_item(sharepoint_client) -> Any:
        return (
            sharepoint_client._client.drives.by_drive_id.return_value.items.by_drive_item_id.return_value
        )

    @staticmethod
    def _create_test_file(
        tmp_path: Path,
        filename: str = "test.txt",
        content: bytes = b"Unit Test",
    ) -> Path:
        test_file = tmp_path / filename
        test_file.write_bytes(content)
        return test_file

    @staticmethod
    def _assert_client_attributes(client: SharePoint, expected_values: dict[str, str]):
        for attr, expected_value in expected_values.items():
            assert getattr(client, attr) == expected_value

    @pytest.fixture
    def mock_env_vars(self) -> dict[str, str]:
        return {
            "SHAREPOINT_CLIENT_ID": DefaultValues.CLIENT_ID.value,
            "SHAREPOINT_CLIENT_SECRET": DefaultValues.CLIENT_SECRET.value,
            "SHAREPOINT_SITE_ID": DefaultValues.SITE_ID.value,
            "SHAREPOINT_TENANT_ID": DefaultValues.TENANT_ID.value,
        }

    @pytest.fixture
    def mock_client_args(self) -> dict[str, str]:
        return {
            "client_id": DefaultValues.CLIENT_ID.value,
            "client_secret": DefaultValues.CLIENT_SECRET.value,
            "site_id": DefaultValues.SITE_ID.value,
            "tenant_id": DefaultValues.TENANT_ID.value,
        }

    @pytest.fixture
    def expected_client_attributes(self) -> dict[str, str]:
        return {
            "base_path": DefaultValues.BASE_PATH.value,
            "client_id": DefaultValues.CLIENT_ID.value,
            "site_id": DefaultValues.SITE_ID.value,
            "tenant_id": DefaultValues.TENANT_ID.value,
        }

    @pytest.fixture
    @patch("open_ire.sharepoint.GraphServiceClient")
    @patch("open_ire.sharepoint.ClientSecretCredential")
    def sharepoint_client(self, mock_credential, mock_graph_client, mock_env_vars):
        with patch.dict(os.environ, mock_env_vars):
            mock_graph_client.return_value = MagicMock()
            client = SharePoint(base_path=DefaultValues.BASE_PATH.value)
            return client

    def test_init_with_args(self, mock_client_args, expected_client_attributes):
        with (
            patch("open_ire.sharepoint.GraphServiceClient"),
            patch("open_ire.sharepoint.ClientSecretCredential"),
        ):
            client = SharePoint(
                base_path=DefaultValues.BASE_PATH.value,
                **mock_client_args,
            )

        self._assert_client_attributes(client, expected_client_attributes)

    def test_init_with_env(self, mock_env_vars, expected_client_attributes):
        with (
            patch.dict(os.environ, mock_env_vars),
            patch("open_ire.sharepoint.GraphServiceClient"),
            patch("open_ire.sharepoint.ClientSecretCredential"),
        ):
            client = SharePoint(base_path=DefaultValues.BASE_PATH.value)

        self._assert_client_attributes(client, expected_client_attributes)

    @pytest.mark.parametrize(
        "env_vars",
        [
            {},
            {
                "SHAREPOINT_CLIENT_ID": DefaultValues.CLIENT_ID.value,
                "SHAREPOINT_TENANT_ID": DefaultValues.TENANT_ID.value,
            },
        ],
    )
    def test_init_missing_params(self, env_vars):
        with patch.dict(os.environ, env_vars, clear=True):
            with pytest.raises(ValueError):
                SharePoint(base_path=DefaultValues.BASE_PATH.value)

    @pytest.mark.parametrize(
        "path,expected",
        [
            (
                "folder/file.txt",
                f"root:/{DefaultValues.BASE_PATH.value}/folder/file.txt:/",
            ),
            (
                "/folder/file.txt",
                f"root:/{DefaultValues.BASE_PATH.value}/folder/file.txt:/",
            ),
            (
                "folder/subfolder/",
                f"root:/{DefaultValues.BASE_PATH.value}/folder/subfolder:/",
            ),
            (
                "/folder/subfolder/",
                f"root:/{DefaultValues.BASE_PATH.value}/folder/subfolder:/",
            ),
        ],
    )
    def test_item_id_from_path(self, sharepoint_client, path: str, expected: str):
        result = sharepoint_client._item_id_from_path(path)
        assert result == expected

    @patch("open_ire.sharepoint.GraphServiceClient")
    @patch("open_ire.sharepoint.ClientSecretCredential")
    def test_authenticate(self, mock_credential, mock_graph_client, sharepoint_client):
        mock_cred_instance = MagicMock()
        mock_credential.return_value = mock_cred_instance
        mock_graph_instance = MagicMock()
        mock_graph_client.return_value = mock_graph_instance

        result = sharepoint_client._authenticate(DefaultValues.CLIENT_SECRET.value)

        mock_credential.assert_called_once_with(
            tenant_id=sharepoint_client.tenant_id,
            client_id=sharepoint_client.client_id,
            client_secret=DefaultValues.CLIENT_SECRET.value,
        )
        mock_graph_client.assert_called_once_with(
            mock_cred_instance, ["https://graph.microsoft.com/.default"]
        )
        assert result == mock_graph_instance

    @pytest.mark.asyncio
    async def test_get_drive_id(self, sharepoint_client):
        mock_drive = self._mock_drive()
        sharepoint_client._client.sites.by_site_id.return_value.drive.get = AsyncMock(
            return_value=mock_drive
        )
        result = await sharepoint_client._get_drive_id()

        assert result == DefaultValues.DRIVE_ID.value
        assert sharepoint_client._drive_id == DefaultValues.DRIVE_ID.value

    @pytest.mark.asyncio
    async def test_get_drive_id_failure(self, sharepoint_client):
        sharepoint_client._client.sites.by_site_id.return_value.drive.get = AsyncMock(
            return_value=None
        )

        with pytest.raises(RuntimeError):
            await sharepoint_client._get_drive_id()

    @pytest.mark.asyncio
    async def test_get_drive_id_cached(self, sharepoint_client):
        sharepoint_client._drive_id = DefaultValues.DRIVE_ID.value
        result = await sharepoint_client._get_drive_id()

        assert result == DefaultValues.DRIVE_ID.value
        sharepoint_client._client.sites.by_site_id.assert_not_called()

    @pytest.mark.asyncio
    @patch("open_ire.sharepoint.LargeFileUploadTask")
    async def test_upload_file(self, mock_task_class, sharepoint_client, tmp_path):
        test_file = self._create_test_file(tmp_path)
        mock_upload_result = MagicMock(spec=UploadResult)
        mock_upload_session = self._mock_upload_session()

        self._mock_client_drive_id(sharepoint_client)
        chain_mock = self._mock_client_drive_item(sharepoint_client)
        chain_mock.create_upload_session.post = AsyncMock(
            return_value=mock_upload_session
        )

        mock_task = MagicMock()
        mock_task.upload = AsyncMock(return_value=mock_upload_result)
        mock_task_class.return_value = mock_task

        result = await sharepoint_client.upload_file(test_file, "uploads/test.txt")

        assert result == mock_upload_result
        mock_task.upload.assert_called_once()

    @pytest.mark.asyncio
    async def test_upload_missing_file(self, sharepoint_client):
        non_existent_file = Path("/non/existent/file.txt")

        with pytest.raises(FileNotFoundError):
            await sharepoint_client.upload_file(non_existent_file, "uploads/test.txt")

    @pytest.mark.asyncio
    async def test_delete_item(self, sharepoint_client):
        self._mock_client_drive_id(sharepoint_client)
        chain_mock = self._mock_client_drive_item(sharepoint_client)
        chain_mock.delete = AsyncMock()

        result = await sharepoint_client.delete_item("folder/file.txt")

        assert result is True
        chain_mock.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_item(self, sharepoint_client):
        mock_drive_item = self._mock_drive_item()

        self._mock_client_drive_id(sharepoint_client)
        chain_mock = self._mock_client_drive_item(sharepoint_client)
        chain_mock.get = AsyncMock(return_value=mock_drive_item)

        result = await sharepoint_client.get_item("folder/test.txt")

        assert result == mock_drive_item
        sharepoint_client._client.drives.by_drive_id.assert_called_once_with(
            DefaultValues.DRIVE_ID.value
        )

    def test_base_path_id(self, sharepoint_client):
        custom_base_path = "unittest/base/path"
        sharepoint_client.base_path = custom_base_path

        result = sharepoint_client._item_id_from_path("folder/file.txt")
        expected = f"root:/{custom_base_path}/folder/file.txt:/"

        assert result == expected
