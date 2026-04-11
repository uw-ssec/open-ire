"""Tests for AuthorIdentifierPipeline."""

from collections.abc import Generator
from unittest.mock import MagicMock

import pytest
from sqlmodel import Session, select

from open_ire.author import ParsedAuthor
from open_ire.items import ArticleItem, AuthorItem
from open_ire.models import Article, Author, Authorship
from open_ire.pipelines.author_identifier_pipeline import AuthorIdentifierPipeline


@pytest.fixture
def pipeline(temp_db: str) -> Generator[AuthorIdentifierPipeline, None, None]:
    """Create a pipeline instance with a test database."""
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
    """Tests for the author identifier pipeline."""

    def test_passes_through_non_author_items(self, pipeline) -> None:
        """Non-AuthorItem items are passed through unchanged."""
        item = MagicMock(spec=ArticleItem)

        result = pipeline.process_item(item)

        assert result is item

    def test_creates_author_with_identifiers(self, pipeline) -> None:
        """New author and identifiers are created."""
        item = AuthorItem(
            author=ParsedAuthor("Eunjung Kim"),
            identifiers=[
                {"authority": "openalex", "identifier": "A5073669402"},
                {"authority": "orcid", "identifier": "0000-0002-4664-9847"},
            ],
        )

        pipeline.process_item(item)

        with Session(pipeline.engine) as session:
            author = session.exec(
                select(Author).where(Author.canonical_name == "Kim, Eunjung")
            ).first()

            assert author is not None
            assert author.first_name == "Eunjung"
            assert author.last_name == "Kim"
            assert author.explicitly_searched is True
            assert len(author.identifiers) == 2

            authorities = {ai.authority for ai in author.identifiers}
            assert authorities == {"openalex", "orcid"}

    def test_finds_existing_author_by_identifier(self, pipeline) -> None:
        """Existing author is found by identifier, not duplicated."""
        # Create an author with the first item
        item1 = AuthorItem(
            author=ParsedAuthor("Eunjung Kim"),
            identifiers=[{"authority": "openalex", "identifier": "A5073669402"}],
        )
        pipeline.process_item(item1)

        # Process the second item with same OpenAlex ID but different name variant
        item2 = AuthorItem(
            author=ParsedAuthor("Eun-Jung Kim"),  # Different name variant
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
            assert author.canonical_name == "Kim, Eunjung"  # Original name preserved
            assert len(author.identifiers) == 2  # ORCID was added

    def test_finds_existing_author_by_name(self, pipeline) -> None:
        """Existing author is found by name, not duplicated."""
        item = AuthorItem(
            author=ParsedAuthor("Test Author"),
            identifiers=[],
        )
        pipeline.process_item(item)

        item = AuthorItem(
            author=ParsedAuthor("Test Author"),
            identifiers=[],
        )
        pipeline.process_item(item)

        with Session(pipeline.engine) as session:
            authors = session.exec(select(Author)).all()
            assert len(authors) == 1

    def test_links_new_author_to_existing_articles(self, pipeline) -> None:
        """A newly-created author is retroactively linked to articles already in the DB."""
        # Pre-populate an article that lists "Hsieh" among its authors.
        with Session(pipeline.engine) as session:
            article = Article(
                title="Existing Article",
                authors="Smith, John; Hsieh, Wei-Lin",
                repository="test_repo",
                reference="EXIST001",
                url="https://example.com/article/exist001",
            )
            session.add(article)
            session.commit()
            article_id = article.id

        # Now create the author Hsieh via the pipeline.
        item = AuthorItem(
            author=ParsedAuthor("Wei-Lin Hsieh"),
            identifiers=[{"authority": "openalex", "identifier": "A9999999999"}],
        )
        pipeline.process_item(item)

        # The pipeline should have retroactively created an Authorship link.
        with Session(pipeline.engine) as session:
            author = session.exec(select(Author).where(Author.last_name == "Hsieh")).first()
            assert author is not None

            link = session.get(Authorship, (article_id, author.id))
            assert link is not None
            assert link.author_order == 1  # second author (0-indexed)
