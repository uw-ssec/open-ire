"""Tests for AuthorshipPipeline."""

from collections.abc import Generator
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlmodel import Session

from open_ire.items import ArticleItem, AuthorItem
from open_ire.models import Article, Author, Authorship
from open_ire.pipelines import AuthorshipPipeline


@pytest.fixture
def pipeline(temp_db: str) -> Generator[AuthorshipPipeline, None, None]:
    """Create a pipeline instance with a test database."""
    db_path = temp_db.replace("sqlite:///", "")
    p = AuthorshipPipeline(db_path)
    p.open_spider()
    yield p
    p.close_spider()


@pytest.fixture
def article_item() -> ArticleItem:
    return ArticleItem(
        repository="imagination",
        reference="the_article",
        authors="hooks, bell; Collins, Patricia Hill; Crenshaw, Kimberlé Williams",
        title="On the Uses of Test Articles",
        publication_date="2005-01-01",
        url="https://example.com/article",
    )


@pytest.fixture
def article() -> Article:
    return Article(
        repository="imagination",
        reference="the_article",
        authors="hooks, bell; Collins, Patricia Hill; Crenshaw, Kimberlé Williams",
        title="On the Uses of Test Articles",
        url="https://example.com/article",
    )


@pytest.fixture
def author() -> Author:
    return Author(
        canonical_name="hooks, bell",
        full_name="bell hooks",
        first_name="bell",
        middle_names="",
        last_name="hooks",
    )


class TestAuthorshipPipeline:
    """Tests for the AuthorshipPipeline class."""

    def test_passes_through_non_author_items(self, pipeline) -> None:
        """Non-ArticleItem items are passed through unchanged."""
        item = MagicMock(spec=AuthorItem)
        result = pipeline.process_item(item)
        assert result is item

    def test_absent_article_raises_exception(self, pipeline, article_item):
        """Non-existent article raises an exception."""
        with pytest.raises(RuntimeError):
            pipeline.process_item(article_item)

    def test_unlinked_article_creates_link(self, pipeline, article_item, article, author):
        """Existing article without an article-author link creates one."""
        with Session(pipeline.engine) as session:
            session.add(article)
            session.add(author)
            session.commit()
            article_id = article.id
            author_id = author.id

        assert article_id is not None
        assert author_id is not None

        pipeline.process_item(article_item)

        with Session(pipeline.engine) as session:
            link = session.get(Authorship, (article_id, author_id))
            assert link is not None
            assert link.author_order == 0

    def test_linked_article_doesnt_create_duplicate_link(
        self, pipeline, article_item, article, author
    ):
        """Link is not duplicated for an existing article with an article-author link."""
        with Session(pipeline.engine) as session:
            session.add(article)
            session.add(author)
            session.commit()
            article_id = article.id
            author_id = author.id

        assert article_id is not None
        assert author_id is not None

        pipeline.process_item(article_item)
        pipeline.process_item(article_item)

        with Session(pipeline.engine) as session:
            links = session.exec(
                select(Authorship).where(
                    Authorship.article_id == article_id,
                    Authorship.author_id == author_id,
                )
            ).all()
            assert len(links) == 1

    def test_authors_not_in_db_are_skipped(self, pipeline, article_item, article):
        """Authors missing from DB are skipped without creating links."""
        with Session(pipeline.engine) as session:
            session.add(article)
            session.commit()
            article_id = article.id

        assert article_id is not None

        pipeline.process_item(article_item)

        with Session(pipeline.engine) as session:
            links = session.exec(
                select(Authorship).where(Authorship.article_id == article_id)
            ).all()
            assert links == []

    def test_only_matched_authors_linked_with_correct_author_order(
        self, pipeline, article_item, article
    ):
        """Author order reflects source position among all parsed authors."""
        with Session(pipeline.engine) as session:
            first_author = Author(
                canonical_name="hooks, bell",
                full_name="bell hooks",
                first_name="bell",
                middle_names="",
                last_name="hooks",
            )
            third_author = Author(
                canonical_name="Crenshaw, Kimberle Williams",
                full_name="Kimberlé Williams Crenshaw",
                first_name="Kimberlé",
                middle_names="Williams",
                last_name="Crenshaw",
            )
            session.add(article)
            session.add(first_author)
            session.add(third_author)
            session.commit()
            article_id = article.id
            first_author_id = first_author.id
            third_author_id = third_author.id

        assert article_id is not None
        assert first_author_id is not None
        assert third_author_id is not None

        pipeline.process_item(article_item)

        with Session(pipeline.engine) as session:
            first_link = session.get(Authorship, (article_id, first_author_id))
            third_link = session.get(Authorship, (article_id, third_author_id))
            assert first_link is not None
            assert third_link is not None
            assert first_link.author_order == 0
            assert third_link.author_order == 2

    def test_existing_link_updates_author_order(self, pipeline, article_item, article, author):
        """Existing links get author_order corrected when it differs."""
        with Session(pipeline.engine) as session:
            session.add(article)
            session.add(author)
            session.commit()
            article_id = article.id
            author_id = author.id
            session.add(Authorship(article_id=article_id, author_id=author_id, author_order=99))
            session.commit()

        assert article_id is not None
        assert author_id is not None

        pipeline.process_item(article_item)

        with Session(pipeline.engine) as session:
            link = session.get(Authorship, (article_id, author_id))
            assert link is not None
            assert link.author_order == 0
