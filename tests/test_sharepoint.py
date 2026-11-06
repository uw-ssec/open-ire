import os
from collections.abc import Iterator
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from kiota_http.httpx_request_adapter import HttpxRequestAdapter
from msgraph.generated.models.drive_item import DriveItem
from msgraph.generated.models.drive_item_collection_response import DriveItemCollectionResponse

from open_ire.sharepoint import SharePointClient

BASE_PATH = "test-base-path"
ARGS = {
    "client_id": "arg-client",
    "tenant_id": "arg-tenant",
    "site_id": "arg-site",
    "client_secret": "arg-secret",
}
ENV = {
    "SHAREPOINT_CLIENT_ID": "env-client",
    "SHAREPOINT_TENANT_ID": "env-tenant",
    "SHAREPOINT_SITE_ID": "env-site",
    "SHAREPOINT_CLIENT_SECRET": "env-secret",
}


@pytest.fixture(autouse=True)
def no_sharepoint_environ() -> Iterator[None]:
    """Remove SharePoint credentials so the developer's shell or .env can't leak in.

    Don't clear the whole environment: on Windows, building the Graph
    client's SSL context then fails.
    """
    with patch.dict(os.environ):
        for name in ENV:
            os.environ.pop(name, None)
        yield


@pytest.mark.parametrize(
    ("args", "env", "expected"),
    [
        (ARGS, {}, ("arg-client", "arg-tenant", "arg-site")),
        ({}, ENV, ("env-client", "env-tenant", "env-site")),
        (ARGS, ENV, ("arg-client", "arg-tenant", "arg-site")),
        (dict.fromkeys(ARGS, ""), ENV, ("env-client", "env-tenant", "env-site")),
        (
            {"client_id": "arg-client", "client_secret": "arg-secret"},
            ENV,
            ("arg-client", "env-tenant", "env-site"),
        ),
    ],
    ids=["args", "env", "args-beat-env", "empty-args-use-env", "mixed"],
)
def test_credentials_resolution(
    args: dict[str, str], env: dict[str, str], expected: tuple[str, str, str]
) -> None:
    os.environ.update(env)

    client = SharePointClient(BASE_PATH, **args)

    assert (client.client_id, client.tenant_id, client.site_id) == expected
    assert client.base_path == BASE_PATH


@pytest.mark.parametrize(
    ("args", "env"),
    [
        ({}, {}),
        ({}, {k: v for k, v in ENV.items() if k != "SHAREPOINT_CLIENT_SECRET"}),
        ({**ARGS, "site_id": ""}, {}),
    ],
    ids=["nothing", "env-without-secret", "empty-arg-without-env"],
)
def test_missing_credentials_raises(args: dict[str, str], env: dict[str, str]) -> None:
    os.environ.update(env)

    with pytest.raises(ValueError, match="Missing required parameters for SharePoint client"):
        SharePointClient(BASE_PATH, **args)


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        (ENV, []),
        ({}, list(ENV)),
        (
            {"SHAREPOINT_CLIENT_ID": "env-client"},
            ["SHAREPOINT_TENANT_ID", "SHAREPOINT_SITE_ID", "SHAREPOINT_CLIENT_SECRET"],
        ),
        ({**ENV, "SHAREPOINT_CLIENT_SECRET": ""}, ["SHAREPOINT_CLIENT_SECRET"]),
    ],
    ids=["all-set", "none-set", "partial", "empty-is-missing"],
)
def test_missing_env_vars(env: dict[str, str], expected: list[str]) -> None:
    os.environ.update(env)

    assert sorted(SharePointClient.missing_env_vars()) == sorted(expected)


@pytest.mark.parametrize("path", ["/folder/file.txt", "folder/file.txt/", "/folder/file.txt/"])
def test_item_id_ignores_outer_slashes(path: str) -> None:
    """Graph rejects a trailing slash, so it must not reach the item ID.

    The live tests verify the item ID syntax; this guards the normalization in CI.
    """
    client = SharePointClient(BASE_PATH, **ARGS)

    assert client._item_id_from_path(path) == client._item_id_from_path("folder/file.txt")


@pytest.mark.asyncio
async def test_list_children() -> None:
    items = [DriveItem(name="a.txt"), DriveItem(name="b.txt")]
    client = SharePointClient(BASE_PATH, **ARGS)
    children = _mock_children(client)
    children.get = AsyncMock(return_value=DriveItemCollectionResponse(value=items))

    assert await client.list_children("folder") == items
    children.with_url.assert_not_called()


@pytest.mark.asyncio
async def test_list_children_follows_next_link() -> None:
    first_page = [DriveItem(name="a.txt")]
    second_page = [DriveItem(name="b.txt")]
    next_link = "https://graph.microsoft.com/next-page"
    client = SharePointClient(BASE_PATH, **ARGS)
    children = _mock_children(client)
    children.get = AsyncMock(
        return_value=DriveItemCollectionResponse(value=first_page, odata_next_link=next_link)
    )
    children.with_url.return_value.get = AsyncMock(
        return_value=DriveItemCollectionResponse(value=second_page)
    )

    assert await client.list_children("folder") == first_page + second_page
    children.with_url.assert_called_once_with(next_link)


@pytest.mark.asyncio
async def test_list_children_empty() -> None:
    client = SharePointClient(BASE_PATH, **ARGS)
    children = _mock_children(client)
    children.get = AsyncMock(return_value=None)

    assert await client.list_children("folder") == []


def _mock_children(client: SharePointClient) -> MagicMock:
    """Swap in a mock Graph client and return the `children` builder for any item."""
    client._drive_id = "drive-id"
    client._client = MagicMock()
    return cast(
        MagicMock,
        client._client.drives.by_drive_id.return_value.items.by_drive_item_id.return_value.children,
    )


@pytest.mark.asyncio
async def test_close_releases_connections() -> None:
    """Use the real Graph client, so an SDK change to how it holds httpx fails here."""
    with patch("open_ire.sharepoint.ClientSecretCredential") as mock_credential:
        client = SharePointClient(BASE_PATH, **ARGS)
    http_client = cast(HttpxRequestAdapter, client._client.request_adapter)._http_client

    with patch.object(httpx.AsyncHTTPTransport, "aclose", autospec=True) as mock_aclose:
        await client.close()

    assert http_client.is_closed
    mock_aclose.assert_awaited()
    mock_credential.return_value.close.assert_called_once_with()
