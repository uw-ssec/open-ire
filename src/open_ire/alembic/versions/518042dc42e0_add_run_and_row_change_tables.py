"""add run and row change tables

Revision ID: 518042dc42e0
Revises: 02243c6a5fdc
Create Date: 2026-11-13 15:41:31.619090

The audit triggers that fill row_change are not created here: env.py recreates
them from the current schema after every migration (see
open_ire.provenance.refresh_audit_triggers). Downgrade drops them, and discards
all recorded runs and row changes.
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "518042dc42e0"
down_revision: str | None = "02243c6a5fdc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("(lower(hex(randomblob(16))))"),
            nullable=False,
        ),
        sa.Column(
            "kind",
            sa.Enum("CRAWL", "MIGRATION", "MANUAL", name="runkind"),
            server_default="MANUAL",
            nullable=False,
        ),
        sa.Column("user", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("notes", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("spider", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("command", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("hostname", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("version", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("commit", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column(
            "started_at",
            sqlmodel.sql.sqltypes.UTCDateTime(),
            server_default=sa.text("(strftime('%Y-%m-%d %H:%M:%f000', 'now'))"),
            nullable=False,
        ),
        sa.Column("finished_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=True),
        sa.Column("finish_reason", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("stats", sa.JSON(), nullable=True),
        sa.CheckConstraint(
            "kind <> 'MANUAL' OR trim(coalesce(notes, '')) <> ''", name="ck_run_manual_notes"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("run", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_run_started_at"), ["started_at"], unique=False)

    op.create_table(
        "row_change",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("table_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("row_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column(
            "operation",
            sa.Enum("INSERT", "UPDATE", "DELETE", name="changeoperation"),
            nullable=False,
        ),
        sa.Column("changes", sa.JSON(), nullable=False),
        sa.Column("changed_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["run.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("row_change", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_row_change_changed_at"), ["changed_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_row_change_run_id"), ["run_id"], unique=False)
        batch_op.create_index(
            "ix_row_change_table_name_row_id", ["table_name", "row_id"], unique=False
        )


def downgrade() -> None:
    # The triggers insert into row_change and read run, so drop them first.
    connection = op.get_bind()
    triggers: Sequence[str] = (
        connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'audit\\_%' ESCAPE '\\'"
        )
        .scalars()
        .all()
    )
    for name in triggers:
        op.execute(f'DROP TRIGGER "{name}"')

    with op.batch_alter_table("row_change", schema=None) as batch_op:
        batch_op.drop_index("ix_row_change_table_name_row_id")
        batch_op.drop_index(batch_op.f("ix_row_change_run_id"))
        batch_op.drop_index(batch_op.f("ix_row_change_changed_at"))

    op.drop_table("row_change")
    with op.batch_alter_table("run", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_run_started_at"))

    op.drop_table("run")
