from pathlib import Path
import pytest
from scrapy.http import Request, TextResponse


@pytest.fixture(scope="session")
def fixtures_dir(pytestconfig) -> Path:
    # pytestconfig.rootpath is the project root (where pytest was invoked)
    return Path(pytestconfig.rootpath) / "tests" / "fixtures"


@pytest.fixture
def response_from_file(fixtures_dir: Path):
    """A fixture that returns a function to create Scrapy responses from a file."""

    def _create_response_from_file(file_path: Path, url: str) -> TextResponse:
        return TextResponse(
            url=url,
            request=Request(url=url),
            body=(fixtures_dir / file_path).read_text(),
            encoding="utf-8",
        )

    return _create_response_from_file
