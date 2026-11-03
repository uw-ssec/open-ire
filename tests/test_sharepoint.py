import os
from collections.abc import Iterator
from typing import cast
from unittest.mock import patch

import httpx
import pytest
from kiota_http.httpx_request_adapter import HttpxRequestAdapter

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
def empty_environ() -> Iterator[None]:
    """Clear the environment so credentials from the developer's shell or .env can't leak in."""
    with patch.dict(os.environ, clear=True):
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
