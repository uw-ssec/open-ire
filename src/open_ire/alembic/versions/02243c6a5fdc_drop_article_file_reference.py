"""drop article file reference table

Revision ID: 02243c6a5fdc
Revises: 9843ce2b584f
Create Date: 2026-09-29 21:57:36.589972

Upgrade discards all stored file references. Downgrade restores only the empty
table; recovering its former rows requires a backup.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "02243c6a5fdc"
down_revision: str | None = "9843ce2b584f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("article_file_reference", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_article_file_reference_created_at"))

    op.drop_table("article_file_reference")


def downgrade() -> None:
    op.create_table(
        "article_file_reference",
        sa.Column("article_id", sa.CHAR(length=32), nullable=True),
        sa.Column("created_at", sa.DATETIME(), nullable=False),
        sa.Column("extension", sa.VARCHAR(), nullable=True),
        sa.Column("size", sa.INTEGER(), nullable=True),
        sa.Column("url", sa.VARCHAR(), nullable=False),
        sa.Column("id", sa.CHAR(length=32), nullable=False),
        sa.Column("source_url", sa.VARCHAR(), nullable=True),
        sa.ForeignKeyConstraint(
            ["article_id"],
            ["article.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("url"),
    )
    with op.batch_alter_table("article_file_reference", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_article_file_reference_created_at"), ["created_at"], unique=False
        )
