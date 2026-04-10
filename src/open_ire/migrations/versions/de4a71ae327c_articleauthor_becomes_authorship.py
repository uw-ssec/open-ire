"""articleauthor becomes authorship

Revision ID: de4a71ae327c
Revises: 0f0e24e01947
Create Date: 2026-04-08 09:38:34.630377
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "de4a71ae327c"
down_revision: str | None = "0f0e24e01947"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.rename_table("articleauthor", "authorship")
    with op.batch_alter_table("authorship", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "updated_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("(datetime('now'))"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("authorship", schema=None) as batch_op:
        batch_op.drop_column("updated_at")
    op.rename_table("authorship", "articleauthor")
