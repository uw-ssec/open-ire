"""Shared fixtures for integration tests."""

import subprocess
from pathlib import Path

import pytest
from sqlmodel import Session, create_engine

from open_ire.db import create_db_engine

# HTTPCACHE_DIR is relative to Scrapy's data dir (.scrapy/), not the project root.
HTTPCACHE_DIR = "httpcache"
HTTPCACHE_DIR_ON_DISK = Path(".scrapy/httpcache")


def pytest_addoption(parser):
    parser.addoption(
        "--author-csv",
        required=False,
        help="Path to faculty CSV for integration tests",
    )


@pytest.fixture
def author_csv(request):
    csv = request.config.getoption("--author-csv")
    if not csv:
        pytest.skip("--author-csv not provided")
    return Path(csv)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Return a temporary database file path."""
    return tmp_path / "test.db"


@pytest.fixture
def db_engine(db_path: Path):
    """Create an SQLite engine with foreign keys enabled."""
    engine = create_engine(f"sqlite:///{db_path}")
    engine = create_db_engine(str(db_path))
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def db_session(db_engine):
    """Create a database session."""
    with Session(db_engine) as session:
        yield session


def run_crawl(
    spider: str, db_path: Path, author_csv: str, extra_args: list[str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run a scrapy crawl as a subprocess with HTTP cache replay.

    Requires cached responses in tests/fixtures/httpcache/.
    Fails on cache miss (no network requests).
    """
    cmd = [
        "python",
        "-m",
        "scrapy",
        "crawl",
        spider,
        "-a",
        f"author_csv={author_csv}",
        "-s",
        f"OPEN_IRE_DATABASE_FILE={db_path}",
        "-s",
        "HTTPCACHE_ENABLED=True",
        "-s",
        f"HTTPCACHE_DIR={HTTPCACHE_DIR}",
        "-s",
        "HTTPCACHE_POLICY=scrapy.extensions.httpcache.DummyPolicy",
        "-s",
        "HTTPCACHE_IGNORE_MISSING=True",
        "-s",
        "LOG_LEVEL=WARNING",
        # Only pipelines that don't need external services:
        "-s",
        'ITEM_PIPELINES={"open_ire.pipelines.DuplicatesPipeline": 1, '
        '"open_ire.pipelines.AuthorIdentifierPipeline": 5, '
        '"open_ire.pipelines.DOINormalizationPipeline": 10, '
        '"open_ire.pipelines.DOIDuplicatesPipeline": 20, '
        '"open_ire.pipelines.SQLModelPipeline": 400, '
        '"open_ire.pipelines.AuthorshipPipeline": 401}',
    ]
    if extra_args:
        cmd.extend(extra_args)

    return subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=120)
