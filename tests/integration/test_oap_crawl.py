"""Integration tests for OAP spiders (OpenAlex and WoS).

These tests replay cached HTTP responses (no network access).
To record/update the cache, run:

    scrapy crawl openalex -a author_csv=<PATH TO AUTHOR CSV> \
        -s HTTPCACHE_ENABLED=True \
        -s HTTPCACHE_DIR=httpcache \
        -s "HTTPCACHE_POLICY=scrapy.extensions.httpcache.DummyPolicy"

HTTPCACHE_DIR is relative to Scrapy's data dir (.scrapy/), not the project root.
"""

from pathlib import Path

import pytest
from sqlmodel import func, select

from open_ire.author import AuthorIndex
from open_ire.models import Article, Author, AuthorIdentifier, Authorship

from .conftest import HTTPCACHE_DIR_ON_DISK, run_crawl

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def require_cache():
    """Skip integration tests if the HTTP cache hasn't been recorded yet."""
    if not HTTPCACHE_DIR_ON_DISK.exists() or not any(HTTPCACHE_DIR_ON_DISK.iterdir()):
        pytest.skip(
            f"HTTP cache not found at {HTTPCACHE_DIR_ON_DISK}. Run a real crawl first to record."
        )


class TestOAPCrawl:
    """Test a full OAP crawl of provided list of authors using cached responses."""

    @pytest.fixture(autouse=True)
    def crawl(self, db_path: Path, db_engine, author_csv):
        """Run the OAP crawl once for all tests in this class."""
        openalex = run_crawl("openalex", db_path, author_csv)
        if openalex.returncode != 0:
            pytest.fail(
                f"OpenAlex crawl failed:\nstdout: {openalex.stdout}\nstderr: {openalex.stderr}"
            )
        wos = run_crawl("wos", db_path, author_csv)
        if wos.returncode != 0:
            pytest.fail(f"WoS crawl failed:\nstdout: {wos.stdout}\nstderr: {wos.stderr}")
        # db_engine fixture ensures tables exist and engine is available
        self._db_engine = db_engine

    def test_articles_collected(self, db_session):
        """Verify that articles were collected."""
        articles = db_session.exec(select(Article)).all()
        assert len(articles) > 0, "No articles were collected"

    def test_authors_created(self, db_session, author_csv: Path):
        """Verify that author records were created for searched faculty."""
        csv_authors = AuthorIndex(Path(author_csv)).records
        db_authors = db_session.exec(select(Author)).all()
        assert len(csv_authors) == len(db_authors), "No explicitly searched authors were created"

    def test_author_identifiers_stored(self, db_session):
        """Verify that identifiers were stored for disambiguated authors."""
        counts = dict(
            db_session.exec(
                select(AuthorIdentifier.authority, func.count()).group_by(
                    AuthorIdentifier.authority
                )
            ).all()
        )
        assert "openalex" in counts, "No OpenAlex identifiers stored"
        assert "email" in counts, "No email addresses stored"
        assert counts["openalex"] > 0
        assert counts["email"] > 0

    def test_all_articles_have_authorships(self, db_session):
        """Verify that article-author links were created."""
        article_count = db_session.exec(select(func.count()).select_from(Article)).one()
        linked_article_count = db_session.exec(
            select(func.count(func.distinct(Authorship.article_id)))
        ).one()
        assert linked_article_count == article_count, (
            f"{article_count - linked_article_count} articles have no authorships"
        )
