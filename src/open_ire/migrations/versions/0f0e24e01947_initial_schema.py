"""initial schema

Revision ID: 0f0e24e01947
Revises:
Create Date: 2026-03-20 22:56:35.328088
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "0f0e24e01947"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _table_exists(name: str) -> bool:
    """Check if a table already exists in the database."""
    bind = op.get_bind()
    return name in inspect(bind).get_table_names()


def upgrade() -> None:
    # Some tables may already exist in databases that pre-date Alembic.
    # Each create_table is guarded so this migration works for both fresh
    # databases and pre-existing ones.

    if not _table_exists("article"):
        op.create_table(
            "article",
            sa.Column("abstract", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column(
                "type", sa.Enum("SCHOLARLY_ARTICLE", "OTHER", name="articletype"), nullable=True
            ),
            sa.Column("authors", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("doi", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column("eissn", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column("isbn", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column("issn", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column("publication_date", sa.Date(), nullable=True),
            sa.Column("reference", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("repository", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("title", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("url", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("extra", sa.JSON(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("repository", "reference", name="uq_article_repository_reference"),
        )
        with op.batch_alter_table("article", schema=None) as batch_op:
            batch_op.create_index(batch_op.f("ix_article_created_at"), ["created_at"], unique=False)
            batch_op.create_index(
                batch_op.f("ix_article_publication_date"), ["publication_date"], unique=False
            )
            batch_op.create_index(batch_op.f("ix_article_reference"), ["reference"], unique=False)
            batch_op.create_index(batch_op.f("ix_article_repository"), ["repository"], unique=False)
            batch_op.create_index(batch_op.f("ix_article_updated_at"), ["updated_at"], unique=False)

    if not _table_exists("author"):
        op.create_table(
            "author",
            sa.Column("first_name", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column("middle_names", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column("last_name", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column("full_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("canonical_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("uw_academic_unit", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column("explicitly_searched", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("id", sa.Integer(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        with op.batch_alter_table("author", schema=None) as batch_op:
            batch_op.create_index(
                batch_op.f("ix_author_canonical_name"), ["canonical_name"], unique=False
            )
            batch_op.create_index(
                batch_op.f("ix_author_explicitly_searched"), ["explicitly_searched"], unique=False
            )
            batch_op.create_index(batch_op.f("ix_author_full_name"), ["full_name"], unique=False)
            batch_op.create_index(
                batch_op.f("ix_author_uw_academic_unit"), ["uw_academic_unit"], unique=False
            )

    if not _table_exists("articleauthor"):
        op.create_table(
            "articleauthor",
            sa.Column("author_order", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("article_id", sa.Uuid(), nullable=False),
            sa.Column("author_id", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["article_id"], ["article.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["author_id"], ["author.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("article_id", "author_id"),
        )

    if not _table_exists("articledepositstatustransition"):
        op.create_table(
            "articledepositstatustransition",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("article_id", sa.Uuid(), nullable=False),
            sa.Column(
                "from_status",
                sa.Enum("PUBLISHED", "READY", "PARTIAL", name="depositstatus"),
                nullable=True,
            ),
            sa.Column(
                "to_status",
                sa.Enum("PUBLISHED", "READY", "PARTIAL", name="depositstatus"),
                nullable=True,
            ),
            sa.Column("changed_at", sa.DateTime(), nullable=False),
            sa.Column("reasons", sa.JSON(), nullable=True),
            sa.ForeignKeyConstraint(["article_id"], ["article.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        with op.batch_alter_table("articledepositstatustransition", schema=None) as batch_op:
            batch_op.create_index(
                batch_op.f("ix_articledepositstatustransition_article_id"),
                ["article_id"],
                unique=False,
            )
            batch_op.create_index(
                batch_op.f("ix_articledepositstatustransition_changed_at"),
                ["changed_at"],
                unique=False,
            )
            batch_op.create_index(
                batch_op.f("ix_articledepositstatustransition_from_status"),
                ["from_status"],
                unique=False,
            )
            batch_op.create_index(
                batch_op.f("ix_articledepositstatustransition_to_status"),
                ["to_status"],
                unique=False,
            )

    if not _table_exists("articlefile"):
        op.create_table(
            "articlefile",
            sa.Column("article_id", sa.Uuid(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("extension", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column("size", sa.Integer(), nullable=True),
            sa.Column("url", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("checksum", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("path", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("store_url", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.ForeignKeyConstraint(["article_id"], ["article.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("url"),
        )
        with op.batch_alter_table("articlefile", schema=None) as batch_op:
            batch_op.create_index(
                batch_op.f("ix_articlefile_created_at"), ["created_at"], unique=False
            )

    if not _table_exists("articlefilereference"):
        op.create_table(
            "articlefilereference",
            sa.Column("article_id", sa.Uuid(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("extension", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column("size", sa.Integer(), nullable=True),
            sa.Column("url", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("source_url", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.ForeignKeyConstraint(["article_id"], ["article.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("url"),
        )
        with op.batch_alter_table("articlefilereference", schema=None) as batch_op:
            batch_op.create_index(
                batch_op.f("ix_articlefilereference_created_at"), ["created_at"], unique=False
            )

    if not _table_exists("articleoaevidence"):
        op.create_table(
            "articleoaevidence",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("article_id", sa.Uuid(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column(
                "kind",
                sa.Enum(
                    "LICENSE",
                    "EXTERNAL_OA",
                    "VERSION",
                    "MANUAL",
                    "FACULTY_AUTHOR",
                    name="oaevidencekind",
                ),
                nullable=False,
            ),
            sa.Column("supports_oa", sa.Boolean(), nullable=False),
            sa.Column("source", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column("data", sa.JSON(), nullable=True),
            sa.ForeignKeyConstraint(["article_id"], ["article.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        with op.batch_alter_table("articleoaevidence", schema=None) as batch_op:
            batch_op.create_index(
                batch_op.f("ix_articleoaevidence_article_id"), ["article_id"], unique=False
            )
            batch_op.create_index(
                batch_op.f("ix_articleoaevidence_created_at"), ["created_at"], unique=False
            )
            batch_op.create_index(batch_op.f("ix_articleoaevidence_kind"), ["kind"], unique=False)

    if not _table_exists("authoraffiliation"):
        op.create_table(
            "authoraffiliation",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("author_id", sa.Integer(), nullable=True),
            sa.Column("year", sa.Integer(), nullable=False),
            sa.CheckConstraint("year >= 1900", name="ck_author_affiliation_year_gte_1900"),
            sa.ForeignKeyConstraint(["author_id"], ["author.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("author_id", "year", name="uq_author_affiliation_author_id_year"),
        )
        with op.batch_alter_table("authoraffiliation", schema=None) as batch_op:
            batch_op.create_index(batch_op.f("ix_authoraffiliation_year"), ["year"], unique=False)

    if not _table_exists("authoridentifier"):
        op.create_table(
            "authoridentifier",
            sa.Column("authority", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("identifier", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("author_id", sa.Integer(), nullable=True),
            sa.ForeignKeyConstraint(["author_id"], ["author.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("authority", "identifier", name="uq_author_identifier"),
        )
        with op.batch_alter_table("authoridentifier", schema=None) as batch_op:
            batch_op.create_index(
                batch_op.f("ix_authoridentifier_authority"), ["authority"], unique=False
            )
            batch_op.create_index(
                batch_op.f("ix_authoridentifier_identifier"), ["identifier"], unique=False
            )


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table("authoridentifier", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_authoridentifier_identifier"))
        batch_op.drop_index(batch_op.f("ix_authoridentifier_authority"))

    op.drop_table("authoridentifier")
    with op.batch_alter_table("authoraffiliation", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_authoraffiliation_year"))

    op.drop_table("authoraffiliation")
    with op.batch_alter_table("articleoaevidence", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_articleoaevidence_kind"))
        batch_op.drop_index(batch_op.f("ix_articleoaevidence_created_at"))
        batch_op.drop_index(batch_op.f("ix_articleoaevidence_article_id"))

    op.drop_table("articleoaevidence")
    with op.batch_alter_table("articlefilereference", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_articlefilereference_created_at"))

    op.drop_table("articlefilereference")
    with op.batch_alter_table("articlefile", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_articlefile_created_at"))

    op.drop_table("articlefile")
    with op.batch_alter_table("articledepositstatustransition", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_articledepositstatustransition_to_status"))
        batch_op.drop_index(batch_op.f("ix_articledepositstatustransition_from_status"))
        batch_op.drop_index(batch_op.f("ix_articledepositstatustransition_changed_at"))
        batch_op.drop_index(batch_op.f("ix_articledepositstatustransition_article_id"))

    op.drop_table("articledepositstatustransition")
    op.drop_table("articleauthor")
    with op.batch_alter_table("author", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_author_uw_academic_unit"))
        batch_op.drop_index(batch_op.f("ix_author_full_name"))
        batch_op.drop_index(batch_op.f("ix_author_explicitly_searched"))
        batch_op.drop_index(batch_op.f("ix_author_canonical_name"))

    op.drop_table("author")
    with op.batch_alter_table("article", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_article_updated_at"))
        batch_op.drop_index(batch_op.f("ix_article_repository"))
        batch_op.drop_index(batch_op.f("ix_article_reference"))
        batch_op.drop_index(batch_op.f("ix_article_publication_date"))
        batch_op.drop_index(batch_op.f("ix_article_created_at"))

    op.drop_table("article")
    # ### end Alembic commands ###
