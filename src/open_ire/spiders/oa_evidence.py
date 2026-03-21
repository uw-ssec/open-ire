import uuid
from typing import Any, Self

from scrapy import Spider
from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from open_ire.db import create_db_engine
from open_ire.enums import DepositStatus, DepositTransitionReason, OAEvidenceKind
from open_ire.models import Article, ArticleDepositStatusTransition, ArticleOAEvidence
from open_ire.settings import OPEN_IRE_CONTACT_EMAIL


class BaseOAEvidenceSpider(Spider):
    custom_settings = {  # noqa: RUF012
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 2.0,
        "DOWNLOAD_DELAY": 1,
        "ITEM_PIPELINES": {},
        "ROBOTSTXT_OBEY": False,
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.engine: Engine | None = None
        self.request_headers = {
            "User-Agent": f"mailto:{OPEN_IRE_CONTACT_EMAIL}",
            "Accept": "application/json",
        }

    @classmethod
    def from_crawler(cls, crawler: Any, *args: Any, **kwargs: Any) -> Self:
        spider = super().from_crawler(crawler, *args, **kwargs)
        db_path = crawler.settings.get("OPEN_IRE_DATABASE_FILE")
        if db_path:
            spider.engine = create_db_engine(db_path)

        return spider

    def closed(self, reason: str) -> None:  # noqa: ARG002
        if self.engine:
            self.engine.dispose()

    def has_oa_evidence(
        self,
        article_id: uuid.UUID,
        *,
        kind: OAEvidenceKind,
        sources: list[str] | None = None,
    ) -> bool:
        if not self.engine:
            return False

        with Session(self.engine) as session:
            statement = select(ArticleOAEvidence).where(
                ArticleOAEvidence.article_id == article_id,
                ArticleOAEvidence.kind == kind,
            )
            if sources:
                statement = statement.where(col(ArticleOAEvidence.source).in_(sources))

            return session.exec(statement).first() is not None

    def has_any_oa_evidence(self, article_id: uuid.UUID) -> bool:
        if not self.engine:
            return False

        with Session(self.engine) as session:
            statement = select(ArticleOAEvidence).where(ArticleOAEvidence.article_id == article_id)
            return session.exec(statement).first() is not None

    def save_oa_evidence(
        self,
        article_id: uuid.UUID,
        *,
        kind: OAEvidenceKind,
        source: str,
        supports_oa: bool,
        data: dict[str, Any] | None = None,
        transition_reason: DepositTransitionReason | None = None,
        allow_transition: bool = True,
    ) -> None:
        if not self.engine:
            self.logger.error("No database engine available")
            return

        with Session(self.engine) as session:
            article = session.get(Article, article_id)
            if not article:
                self.logger.warning("Article %s not found in database", article_id)
                return

            current_status = article.deposit_status

            evidence = ArticleOAEvidence(
                article_id=article_id,
                kind=kind,
                supports_oa=supports_oa,
                source=source,
                data=data or {},
            )
            session.add(evidence)

            if (
                supports_oa
                and allow_transition
                and transition_reason
                and current_status not in (DepositStatus.READY, DepositStatus.PUBLISHED)
            ):
                transition = ArticleDepositStatusTransition(
                    article_id=article_id,
                    from_status=current_status,
                    to_status=DepositStatus.READY,
                    reasons=[transition_reason],
                )
                session.add(transition)
                self.logger.info(
                    "Article %s transitioned to READY based on %s evidence from %s",
                    article_id,
                    kind,
                    source,
                )

            session.commit()
