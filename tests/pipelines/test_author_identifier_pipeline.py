"""Tests for AuthorIdentifierPipeline."""

from collections.abc import Generator
from unittest.mock import MagicMock

import pytest
from sqlmodel import Session, select

from open_ire.items import ArticleItem, AuthorItem
from open_ire.models import Author
from open_ire.pipelines.author_identifier_pipeline import AuthorIdentifierPipeline


@pytest.fixture
def pipeline(temp_db: str) -> Generator[AuthorIdentifierPipeline, None, None]:
    """Create a pipeline instance with test database."""
    db_path = temp_db.replace("sqlite:///", "")
    p = AuthorIdentifierPipeline(db_path)
    p.open_spider()
    yield p
    p.close_spider()


@pytest.fixture
def mock_spider() -> MagicMock:
    spider = MagicMock()
    spider.name = "openalex"
    return spider


class TestAuthorIdentifierPipeline:
    """Tests for author identifier storage pipeline."""

    def test_passes_through_non_author_items(self, pipeline) -> None:
        """Non-AuthorItem items are passed through unchanged."""
        item = MagicMock(spec=ArticleItem)

        result = pipeline.process_item(item)

        assert result is item

    def test_creates_author_with_identifiers(self, pipeline) -> None:
        """New author and identifiers are created."""
        item = AuthorItem(
            full_name="Eunjung Kim",
            first_name="Eunjung",
            last_name="Kim",
            identifiers=[
                {"authority": "openalex", "identifier": "A5073669402"},
                {"authority": "orcid", "identifier": "0000-0002-4664-9847"},
            ],
        )

        pipeline.process_item(item)

        with Session(pipeline.engine) as session:
            author = session.exec(select(Author).where(Author.full_name == "Eunjung Kim")).first()

            assert author is not None
            assert author.first_name == "Eunjung"
            assert author.last_name == "Kim"
            assert author.explicitly_searched is True
            assert len(author.identifiers) == 2

            authorities = {ai.authority for ai in author.identifiers}
            assert authorities == {"openalex", "orcid"}

    def test_finds_existing_author_by_identifier(self, pipeline) -> None:
        """Existing author is found by identifier, not duplicated."""
        # Create author with first item
        item1 = AuthorItem(
            full_name="Eunjung Kim",
            identifiers=[{"authority": "openalex", "identifier": "A5073669402"}],
        )
        pipeline.process_item(item1)

        # Process second item with same OpenAlex ID but different name variant
        item2 = AuthorItem(
            full_name="Eun-Jung Kim",  # Different name variant
            identifiers=[
                {"authority": "openalex", "identifier": "A5073669402"},
                {"authority": "orcid", "identifier": "0000-0002-4664-9847"},
            ],
        )
        pipeline.process_item(item2)

        with Session(pipeline.engine) as session:
            authors = session.exec(select(Author)).all()
            assert len(authors) == 1  # No duplicate created

            author = authors[0]
            assert author.full_name == "Eunjung Kim"  # Original name preserved
            assert len(author.identifiers) == 2  # ORCID was added

    def test_skips_item_without_identifiers(self, pipeline) -> None:
        """Items without identifiers are skipped."""
        item = AuthorItem(
            full_name="Test Author",
            identifiers=[],
        )

        pipeline.process_item(item)

        with Session(pipeline.engine) as session:
            authors = session.exec(select(Author)).all()
            assert len(authors) == 0
