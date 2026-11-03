"""Live tests of SharePointClient against the development SharePoint folder.

These talk to the real Graph API and need the SharePoint credentials from .env.
Each test works in its own folder under the development base path and deletes
it afterward. Run them after changing open_ire.sharepoint or upgrading msgraph-sdk:

    pytest -m integration tests/integration/test_sharepoint.py
"""

import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from msgraph.generated.models.o_data_errors.o_data_error import ODataError

from open_ire.settings.development import OPEN_IRE_SHAREPOINT_BASE_PATH
from open_ire.sharepoint import SharePointClient

pytestmark = pytest.mark.integration

# The smallest chunk Graph allows, to keep multi-chunk uploads quick.
CHUNK = 320 * 1024


@pytest_asyncio.fixture
async def sharepoint_folder(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[SharePoint, str]]:
    if missing := SharePoint.missing_credentials():
        pytest.skip(f"SharePoint credentials not set: {', '.join(missing)}")

    monkeypatch.setattr(SharePoint, "_UPLOAD_CHUNK_SIZE", CHUNK)
    sharepoint = SharePoint(base_path=OPEN_IRE_SHAREPOINT_BASE_PATH)
    folder = f"live_tests/{uuid.uuid4().hex}"
    try:
        yield sharepoint, folder
        await sharepoint.delete_item(folder)
    finally:
        await sharepoint.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [0, 9, CHUNK, CHUNK + 1, int(CHUNK * 2.5)], ids=str)
async def test_upload_round_trip(
    sharepoint_folder: tuple[SharePointClient, str], tmp_path: Path, size: int
) -> None:
    """Upload a file, then replace it, checking the stored bytes each time.

    The download goes into a folder that doesn't exist yet, so it must be created.
    """
    sharepoint, folder = sharepoint_folder
    remote_path = f"{folder}/upload.bin"
    local_path = tmp_path / "upload.bin"
    downloaded_path = tmp_path / "missing_dir" / "downloaded.bin"

    for attempt in ("create", "replace"):
        content = os.urandom(size)
        local_path.write_bytes(content)

        result = await sharepoint.upload_file(local_path, remote_path)
        await sharepoint.download_file(remote_path, downloaded_path)

        assert result.location, attempt
        assert downloaded_path.read_bytes() == content, attempt


@pytest.mark.asyncio
async def test_outer_slashes_are_ignored(
    sharepoint_folder: tuple[SharePointClient, str], tmp_path: Path
) -> None:
    """Leading and trailing slashes address the same item as the bare path."""
    sharepoint, folder = sharepoint_folder
    local_path = tmp_path / "slashes.txt"
    downloaded_path = tmp_path / "downloaded.txt"
    local_path.write_bytes(b"slashes")

    await sharepoint.upload_file(local_path, f"/{folder}/slashes.txt")
    await sharepoint.download_file(f"{folder}/slashes.txt/", downloaded_path)
    folder_item = await sharepoint.get_item(f"/{folder}/")

    assert downloaded_path.read_bytes() == b"slashes"
    assert folder_item is not None
    assert folder_item.folder is not None


@pytest.mark.asyncio
async def test_delete_removes_folder_and_contents(
    sharepoint_folder: tuple[SharePointClient, str], tmp_path: Path
) -> None:
    sharepoint, folder = sharepoint_folder
    local_path = tmp_path / "doomed.txt"
    local_path.write_bytes(b"doomed")
    await sharepoint.upload_file(local_path, f"{folder}/subfolder/doomed.txt")

    await sharepoint.delete_item(f"{folder}/subfolder")

    with pytest.raises(ODataError) as excinfo:
        await sharepoint.get_item(f"{folder}/subfolder/doomed.txt")
    assert excinfo.value.response_status_code == 404
