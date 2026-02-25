import uuid
from collections.abc import Generator
from datetime import date
from typing import Any

import pytest
from sqlmodel import Session, SQLModel, create_engine

from open_ire.models import Article


@pytest.fixture
def spider_with_db(spider: Any) -> Generator[Any, None, None]:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    spider.engine = engine
    yield spider
    engine.dispose()


@pytest.fixture
def article_id() -> uuid.UUID:
    return uuid.UUID("12345678-1234-5678-1234-567812345678")


@pytest.fixture
def sample_article(spider_with_db: Any, article_id: uuid.UUID) -> Article:
    engine = getattr(spider_with_db, "engine", None)
    if engine is None:
        msg = "Expected spider_with_db to expose a database engine"
        raise RuntimeError(msg)

    with Session(engine) as session:
        article = Article(
            id=article_id,
            title="Test Article",
            authors="Test Author",
            publication_date=date(2023, 1, 15),
            repository="test_repo",
            reference="TEST001",
            url="https://example.com/article",
            doi="10.1234/test.doi",
        )
        session.add(article)
        session.commit()
        session.refresh(article)
        return article
