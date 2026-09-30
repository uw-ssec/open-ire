"""Make table names use snake_case

Revision ID: 473c9757ac3f
Revises: de4a71ae327c
Create Date: 2026-07-29 15:10:32.483572
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "473c9757ac3f"
down_revision: str | None = "de4a71ae327c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.rename_table("articledepositstatustransition", "article_deposit_status_transition")
    op.rename_table("articlefile", "article_file")
    op.rename_table("articlefilereference", "article_file_reference")
    op.rename_table("articleoaevidence", "article_oa_evidence")
    op.rename_table("authoraffiliation", "author_affiliation")
    op.rename_table("authoridentifier", "author_identifier")

    with op.batch_alter_table("article_deposit_status_transition", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_articledepositstatustransition_article_id"))
        batch_op.drop_index(batch_op.f("ix_articledepositstatustransition_changed_at"))
        batch_op.drop_index(batch_op.f("ix_articledepositstatustransition_from_status"))
        batch_op.drop_index(batch_op.f("ix_articledepositstatustransition_to_status"))
        batch_op.create_index(
            batch_op.f("ix_article_deposit_status_transition_article_id"),
            ["article_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_article_deposit_status_transition_changed_at"),
            ["changed_at"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_article_deposit_status_transition_from_status"),
            ["from_status"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_article_deposit_status_transition_to_status"),
            ["to_status"],
            unique=False,
        )

    with op.batch_alter_table("article_file", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_articlefile_created_at"))
        batch_op.create_index(
            batch_op.f("ix_article_file_created_at"), ["created_at"], unique=False
        )

    with op.batch_alter_table("article_file_reference", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_articlefilereference_created_at"))
        batch_op.create_index(
            batch_op.f("ix_article_file_reference_created_at"), ["created_at"], unique=False
        )

    with op.batch_alter_table("article_oa_evidence", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_articleoaevidence_article_id"))
        batch_op.drop_index(batch_op.f("ix_articleoaevidence_created_at"))
        batch_op.drop_index(batch_op.f("ix_articleoaevidence_kind"))
        batch_op.create_index(
            batch_op.f("ix_article_oa_evidence_article_id"), ["article_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_article_oa_evidence_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_article_oa_evidence_kind"), ["kind"], unique=False)

    with op.batch_alter_table("author_affiliation", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_authoraffiliation_year"))
        batch_op.create_index(batch_op.f("ix_author_affiliation_year"), ["year"], unique=False)

    with op.batch_alter_table("author_identifier", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_authoridentifier_authority"))
        batch_op.drop_index(batch_op.f("ix_authoridentifier_identifier"))
        batch_op.create_index(
            batch_op.f("ix_author_identifier_authority"), ["authority"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_author_identifier_identifier"), ["identifier"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("author_identifier", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_author_identifier_identifier"))
        batch_op.drop_index(batch_op.f("ix_author_identifier_authority"))
        batch_op.create_index(
            batch_op.f("ix_authoridentifier_identifier"), ["identifier"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_authoridentifier_authority"), ["authority"], unique=False
        )

    with op.batch_alter_table("author_affiliation", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_author_affiliation_year"))
        batch_op.create_index(batch_op.f("ix_authoraffiliation_year"), ["year"], unique=False)

    with op.batch_alter_table("article_oa_evidence", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_article_oa_evidence_kind"))
        batch_op.drop_index(batch_op.f("ix_article_oa_evidence_created_at"))
        batch_op.drop_index(batch_op.f("ix_article_oa_evidence_article_id"))
        batch_op.create_index(batch_op.f("ix_articleoaevidence_kind"), ["kind"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_articleoaevidence_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_articleoaevidence_article_id"), ["article_id"], unique=False
        )

    with op.batch_alter_table("article_file_reference", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_article_file_reference_created_at"))
        batch_op.create_index(
            batch_op.f("ix_articlefilereference_created_at"), ["created_at"], unique=False
        )

    with op.batch_alter_table("article_file", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_article_file_created_at"))
        batch_op.create_index(batch_op.f("ix_articlefile_created_at"), ["created_at"], unique=False)

    with op.batch_alter_table("article_deposit_status_transition", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_article_deposit_status_transition_to_status"))
        batch_op.drop_index(batch_op.f("ix_article_deposit_status_transition_from_status"))
        batch_op.drop_index(batch_op.f("ix_article_deposit_status_transition_changed_at"))
        batch_op.drop_index(batch_op.f("ix_article_deposit_status_transition_article_id"))
        batch_op.create_index(
            batch_op.f("ix_articledepositstatustransition_to_status"), ["to_status"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_articledepositstatustransition_from_status"),
            ["from_status"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_articledepositstatustransition_changed_at"), ["changed_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_articledepositstatustransition_article_id"), ["article_id"], unique=False
        )

    op.rename_table("author_identifier", "authoridentifier")
    op.rename_table("author_affiliation", "authoraffiliation")
    op.rename_table("article_oa_evidence", "articleoaevidence")
    op.rename_table("article_file_reference", "articlefilereference")
    op.rename_table("article_file", "articlefile")
    op.rename_table("article_deposit_status_transition", "articledepositstatustransition")
