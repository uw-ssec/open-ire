"""Tests for author normalization models."""

from datetime import date
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, create_engine, select

from open_ire.models import Article, Author, AuthorAffiliation, AuthorIdentifier, Authorship


@pytest.fixture
def engine():
    """Create an in-memory SQLite engine for testing."""
    engine = create_engine("sqlite:///:memory:")
    event.listen(
        engine,
        "connect",
        lambda dbapi_connection, _: dbapi_connection.execute("PRAGMA foreign_keys=ON"),
    )
    SQLModel.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def session(engine):
    """Create a database session for testing."""
    with Session(engine) as session:
        yield session


class TestAuthorship:
    """Tests for article-author junction relationships and constraints."""

    def test_many_to_many_relationship_integrity(self, session: Session):
        """Property 1: Many-to-many relationship integrity.

        For any article and author pair, when they are linked through the
        Authorship junction table, both the article and author should be
        accessible through their respective relationship properties.

        **Feature: author-normalization, Property 1: Many-to-many relationship integrity**
        **Validates: Requirements 1.2**
        """
        # Create test data
        article = Article(
            title="Test Article",
            authors="Test Author",
            publication_date=date(2025, 1, 8),
            repository="test_repo",
            reference="test_ref_001",
            url="https://example.com/test",
        )

        author = Author(
            full_name="Test Author",
            first_name="Test",
            last_name="Author",
            canonical_name="Author, Test",
        )

        # Add to session to get IDs
        session.add(article)
        session.add(author)
        session.commit()
        session.refresh(article)
        session.refresh(author)

        # Create junction record
        article_author = Authorship(article_id=article.id, author_id=author.id, author_order=1)

        session.add(article_author)
        session.commit()

        # Test forward relationship: article -> junction -> author
        session.refresh(article)
        assert len(article.authorships) == 1
        assert article.authorships[0].author_id == author.id
        assert article.authorships[0].author_order == 1

        # Test backward relationship: author -> junction -> article
        session.refresh(author)
        assert len(author.authorships) == 1
        assert author.authorships[0].article_id == article.id

        # Test junction table relationships
        session.refresh(article_author)
        assert article_author.article.id == article.id
        assert article_author.author.id == author.id
        assert article_author.article.title == "Test Article"
        assert article_author.author.full_name == "Test Author"

    def test_multiple_authors_per_article(self, session: Session):
        """Test that an article can have multiple authors."""
        article = Article(
            title="Multi-Author Article",
            authors="Author One, Author Two",
            publication_date=date(2025, 1, 8),
            repository="test_repo",
            reference="test_ref_002",
            url="https://example.com/test2",
        )

        author1 = Author(
            full_name="Author One",
            first_name="Author",
            last_name="One",
            canonical_name="One, Author",
        )
        author2 = Author(
            full_name="Author Two",
            first_name="Author",
            last_name="Two",
            canonical_name="Two, Author",
        )

        session.add_all([article, author1, author2])
        session.commit()
        session.refresh(article)
        session.refresh(author1)
        session.refresh(author2)

        # Create junction records
        junction1 = Authorship(article_id=article.id, author_id=author1.id, author_order=1)
        junction2 = Authorship(article_id=article.id, author_id=author2.id, author_order=2)

        session.add_all([junction1, junction2])
        session.commit()

        # Verify relationships through junction table
        session.refresh(article)
        assert len(article.authorships) == 2
        author_ids = {junction.author_id for junction in article.authorships}
        assert author_ids == {author1.id, author2.id}

    def test_multiple_articles_per_author(self, session: Session):
        """Test that an author can be associated with multiple articles."""
        author = Author(
            full_name="Prolific Author",
            first_name="Prolific",
            last_name="Author",
            canonical_name="Author, Prolific",
        )

        article1 = Article(
            title="First Article",
            authors="Prolific Author",
            publication_date=date(2025, 1, 8),
            repository="test_repo",
            reference="test_ref_003",
            url="https://example.com/test3",
        )

        article2 = Article(
            title="Second Article",
            authors="Prolific Author",
            publication_date=date(2025, 1, 8),
            repository="test_repo",
            reference="test_ref_004",
            url="https://example.com/test4",
        )

        session.add_all([author, article1, article2])
        session.commit()
        session.refresh(author)
        session.refresh(article1)
        session.refresh(article2)

        # Create junction records
        junction1 = Authorship(article_id=article1.id, author_id=author.id, author_order=1)
        junction2 = Authorship(article_id=article2.id, author_id=author.id, author_order=1)

        session.add_all([junction1, junction2])
        session.commit()

        # Verify relationships through junction table
        session.refresh(author)
        assert len(author.authorships) == 2
        article_ids = {junction.article_id for junction in author.authorships}
        assert article_ids == {article1.id, article2.id}

    def test_delete_junction_keeps_article_and_author(self, session: Session):
        """Test that relationships are maintained when objects are deleted."""
        # Create test data
        article = Article(
            title="Cascade Test Article",
            authors="Cascade Author",
            publication_date=date(2025, 1, 8),
            repository="test_repo",
            reference="test_ref_005",
            url="https://example.com/test5",
        )

        author = Author(
            full_name="Cascade Author",
            first_name="Cascade",
            last_name="Author",
            canonical_name="Author, Cascade",
        )

        session.add_all([article, author])
        session.commit()
        session.refresh(article)
        session.refresh(author)

        # Create junction record
        junction = Authorship(article_id=article.id, author_id=author.id, author_order=1)
        session.add(junction)
        session.commit()

        # Verify initial state
        assert len(session.exec(select(Authorship)).all()) == 1

        # Delete the junction record directly
        session.delete(junction)
        session.commit()

        # Verify junction record is gone but article and author remain
        assert len(session.exec(select(Authorship)).all()) == 0
        assert session.get(Article, article.id) is not None
        assert session.get(Author, author.id) is not None

    def test_delete_article_removes_junction_records(self, session: Session):
        """Deleting an article should remove related article-author junction rows."""
        article = Article(
            title="Delete Article Cascade",
            authors="Cascade Author",
            publication_date=date(2025, 1, 8),
            repository="test_repo",
            reference="test_ref_006",
            url="https://example.com/test6",
        )
        author = Author(
            full_name="Cascade Author",
            first_name="Cascade",
            last_name="Author",
            canonical_name="Author, Cascade",
        )
        session.add_all([article, author])
        session.commit()
        session.refresh(article)
        session.refresh(author)

        session.add(Authorship(article_id=article.id, author_id=author.id, author_order=1))
        session.commit()
        assert len(session.exec(select(Authorship)).all()) == 1

        session.delete(article)
        session.commit()

        assert len(session.exec(select(Authorship)).all()) == 0
        assert session.get(Author, author.id) is not None

    def test_delete_author_removes_junction_records(self, session: Session):
        """Deleting an author should remove related article-author junction rows."""
        article = Article(
            title="Delete Author Cascade",
            authors="Cascade Author",
            publication_date=date(2025, 1, 8),
            repository="test_repo",
            reference="test_ref_007",
            url="https://example.com/test7",
        )
        author = Author(
            full_name="Cascade Author",
            first_name="Cascade",
            last_name="Author",
            canonical_name="Author, Cascade",
        )
        session.add_all([article, author])
        session.commit()
        session.refresh(article)
        session.refresh(author)

        session.add(Authorship(article_id=article.id, author_id=author.id, author_order=1))
        session.commit()
        assert len(session.exec(select(Authorship)).all()) == 1

        session.delete(author)
        session.commit()

        assert len(session.exec(select(Authorship)).all()) == 0
        assert session.get(Article, article.id) is not None

    def test_authorship_rejects_missing_article(self, session: Session):
        """Verify SQLite foreign key constraints are active (PRAGMA foreign_keys=ON)."""
        author = Author(
            full_name="Test Author",
            first_name="Test",
            last_name="Author",
            canonical_name="Author, Test",
        )
        session.add(author)
        session.commit()
        session.refresh(author)

        session.add(Authorship(article_id=uuid4(), author_id=author.id, author_order=1))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


class TestAuthorIdentifiers:
    """Tests for author identifier relationships and constraints."""

    def test_author_identifiers_relationship(self, session: Session):
        """Test that author identifiers are properly linked to authors."""
        author = Author(
            full_name="Identified Author",
            first_name="Identified",
            last_name="Author",
            canonical_name="Author, Identified",
        )

        session.add(author)
        session.commit()
        session.refresh(author)

        # Add identifiers
        orcid = AuthorIdentifier(
            author_id=author.id, authority="ORCID", identifier="0000-0000-0000-0001"
        )

        institutional_id = AuthorIdentifier(
            author_id=author.id, authority="UW", identifier="uw123456"
        )

        session.add_all([orcid, institutional_id])
        session.commit()

        # Verify relationship
        session.refresh(author)
        assert len(author.identifiers) == 2

        identifier_pairs = {(id.authority, id.identifier) for id in author.identifiers}
        expected_pairs = {("ORCID", "0000-0000-0000-0001"), ("UW", "uw123456")}
        assert identifier_pairs == expected_pairs

    def test_delete_author_removes_identifiers(self, session: Session):
        """Deleting an author should remove related identifier rows."""
        author = Author(
            full_name="Identified Author",
            first_name="Identified",
            last_name="Author",
            canonical_name="Author, Identified",
        )
        session.add(author)
        session.commit()
        session.refresh(author)

        session.add_all(
            [
                AuthorIdentifier(
                    author_id=author.id, authority="ORCID", identifier="0000-0000-0000-0002"
                ),
                AuthorIdentifier(author_id=author.id, authority="UW", identifier="uw654321"),
            ]
        )
        session.commit()
        assert len(session.exec(select(AuthorIdentifier)).all()) == 2

        session.delete(author)
        session.commit()

        assert len(session.exec(select(AuthorIdentifier)).all()) == 0


class TestAuthorConstraints:
    """Tests for constraints on the Author model itself."""

    def test_author_requires_canonical_name(self, session: Session):
        """Creating an author without canonical_name should fail at commit time."""
        author = Author(full_name="Missing Canonical", first_name="Missing", last_name="Canonical")
        session.add(author)

        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


class TestAuthorAffiliations:
    def test_author_affiliation_relationship(self, session: Session):
        author = Author(
            full_name="Affiliated Author",
            first_name="Affiliated",
            last_name="Author",
            canonical_name="Author, Affiliated",
        )
        session.add(author)
        session.commit()
        session.refresh(author)

        affiliation1 = AuthorAffiliation(author_id=author.id, year=2019)
        affiliation2 = AuthorAffiliation(author_id=author.id, year=2021)
        session.add_all([affiliation1, affiliation2])
        session.commit()

        session.refresh(author)
        assert sorted(a.year for a in author.affiliations) == [2019, 2021]

        session.refresh(affiliation1)
        assert affiliation1.author.id == author.id

    def test_delete_author_removes_affiliations(self, session: Session):
        """Deleting an author should remove related affiliation rows."""
        author = Author(
            full_name="Affiliated Author",
            first_name="Affiliated",
            last_name="Author",
            canonical_name="Author, Affiliated",
        )
        session.add(author)
        session.commit()
        session.refresh(author)

        session.add_all(
            [
                AuthorAffiliation(author_id=author.id, year=2019),
                AuthorAffiliation(author_id=author.id, year=2021),
            ]
        )
        session.commit()
        assert len(session.exec(select(AuthorAffiliation)).all()) == 2

        session.delete(author)
        session.commit()

        assert len(session.exec(select(AuthorAffiliation)).all()) == 0

    def test_author_affiliation_year_validation(self):
        with pytest.raises(ValidationError):
            AuthorAffiliation.model_validate({"author_id": 1, "year": 1899})

    def test_author_affiliation_year_check_constraint(self, session: Session):
        author = Author(
            full_name="Bounded Year Author",
            first_name="Bounded",
            last_name="Author",
            canonical_name="Author, Bounded",
        )
        session.add(author)
        session.commit()
        session.refresh(author)

        session.add(AuthorAffiliation(author_id=author.id, year=1899))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

    def test_author_affiliation_unique_author_year(self, session: Session):
        author = Author(
            full_name="Unique Year Author",
            first_name="Unique",
            last_name="Author",
            canonical_name="Author, Unique",
        )
        session.add(author)
        session.commit()
        session.refresh(author)

        session.add(AuthorAffiliation(author_id=author.id, year=2020))
        session.commit()

        session.add(AuthorAffiliation(author_id=author.id, year=2020))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
