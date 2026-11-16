"""Tests for runs and the audit triggers that record row changes."""

import sqlite3
import subprocess
import uuid
from collections.abc import Iterator, Sequence
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Column, Engine, String
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from open_ire.db import create_db_engine
from open_ire.enums import ChangeOperation, RunKind
from open_ire.errors import OpenRunError
from open_ire.models import Article, ArticleFile, Author, Authorship, RowChange, Run
from open_ire.provenance import (
    begin_run,
    drop_audit_triggers,
    finish_run,
    git_commit,
    manual_run,
    refresh_audit_triggers,
)

INSERT_AUTHOR = (
    "INSERT INTO author (full_name, canonical_name, explicitly_searched, created_at, updated_at) "
    "VALUES ('Jane Doe', 'doe jane', 0, '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "open_ire.db"


@pytest.fixture
def engine(db_path: Path) -> Iterator[Engine]:
    """An engine on a database migrated to head, which installs the audit triggers."""
    engine = create_db_engine(str(db_path))
    yield engine
    engine.dispose()


@pytest.fixture
def run_id(engine: Engine) -> uuid.UUID:
    """The ID of an open crawl run."""
    with engine.begin() as connection:
        return begin_run(connection, RunKind.CRAWL, spider="test_spider")


class TestRequiredRun:
    def test_writes_without_an_open_run_are_rejected(self, engine: Engine) -> None:
        with Session(engine) as session:
            session.add(_author())
            with pytest.raises(IntegrityError, match="No run is open"):
                session.commit()

    @pytest.mark.usefixtures("run_id")
    def test_writes_after_the_run_finishes_are_rejected(self, engine: Engine) -> None:
        with Session(engine) as session:
            author = _author()
            session.add(author)
            session.commit()
            _finish_open_run(engine)

            author.full_name = "John Doe"
            with pytest.raises(IntegrityError, match="No run is open"):
                session.commit()

    @pytest.mark.usefixtures("run_id")
    def test_only_one_run_can_be_open(self, engine: Engine, db_path: Path) -> None:
        with engine.begin() as connection, pytest.raises(OpenRunError, match="still open"):
            begin_run(connection, RunKind.CRAWL)

        with (
            closing(sqlite3.connect(db_path)) as connection,
            pytest.raises(sqlite3.IntegrityError, match="Another run is open"),
        ):
            connection.execute("INSERT INTO run (user, notes) VALUES ('me', 'why')")

    def test_manual_runs_require_notes(self, engine: Engine, db_path: Path) -> None:
        with (
            closing(sqlite3.connect(db_path)) as connection,
            pytest.raises(sqlite3.IntegrityError, match="ck_run_manual_notes"),
        ):
            connection.execute("INSERT INTO run (user, notes) VALUES ('me', '  ')")


class TestAppendOnlyRunTables:
    @pytest.fixture
    def closed_run_id(self, engine: Engine) -> uuid.UUID:
        with engine.begin() as connection:
            run_id = begin_run(connection, RunKind.CRAWL)
            finish_run(connection, run_id, "finished")
        return run_id

    @pytest.mark.parametrize(
        "statement",
        [
            "UPDATE run SET finished_at = NULL",
            "UPDATE run SET notes = 'a different reason'",
            "UPDATE run SET finished_at = CURRENT_TIMESTAMP, finish_reason = 'again'",
            "DELETE FROM run",
        ],
    )
    @pytest.mark.usefixtures("closed_run_id")
    def test_a_closed_run_cannot_be_changed(self, db_path: Path, statement: str) -> None:
        with (
            closing(sqlite3.connect(db_path)) as connection,
            pytest.raises(sqlite3.IntegrityError, match="can't be changed once closed"),
        ):
            connection.execute(statement)

    @pytest.mark.parametrize(
        "statement",
        [
            "UPDATE run SET notes = 'a different reason'",
            "UPDATE run SET finished_at = CURRENT_TIMESTAMP, user = 'someone else'",
            "DELETE FROM run",
        ],
    )
    @pytest.mark.usefixtures("run_id")
    def test_an_open_run_can_only_be_closed(self, db_path: Path, statement: str) -> None:
        with (
            closing(sqlite3.connect(db_path)) as connection,
            pytest.raises(sqlite3.IntegrityError, match="open run can only be closed"),
        ):
            connection.execute(statement)

    def test_closing_records_reason_and_stats(self, engine: Engine, run_id: uuid.UUID) -> None:
        with engine.begin() as connection:
            finish_run(connection, run_id, "shutdown", {"item_scraped_count": 3})

        with Session(engine) as session:
            run = session.get_one(Run, run_id)
        assert (run.finish_reason, run.stats) == ("shutdown", {"item_scraped_count": 3})
        assert run.finished_at is not None

    @pytest.mark.parametrize(
        "statement", ["UPDATE row_change SET changes = '{}'", "DELETE FROM row_change"]
    )
    @pytest.mark.usefixtures("run_id")
    def test_row_changes_cannot_be_changed(
        self, engine: Engine, db_path: Path, statement: str
    ) -> None:
        with Session(engine) as session:
            session.add(_author())
            session.commit()

        with (
            closing(sqlite3.connect(db_path)) as connection,
            pytest.raises(sqlite3.IntegrityError, match="audit record"),
        ):
            connection.execute(statement)


class TestDatabaseBrowserEdits:
    """A person editing the database in a client that knows nothing of Open IRE."""

    def test_a_manual_run_records_raw_sql_writes(self, engine: Engine, db_path: Path) -> None:
        with Session(engine) as session:
            session.add(_author())
            # Seeding needs a run too; close it so the browser can open its own.
            with engine.begin() as connection:
                seed = begin_run(connection, RunKind.CRAWL)
            session.commit()
            with engine.begin() as connection:
                finish_run(connection, seed, "finished")

        with closing(sqlite3.connect(db_path)) as connection:
            connection.execute("INSERT INTO run (user, notes) VALUES ('me', 'fix a typo')")
            connection.execute("UPDATE author SET full_name = 'Jane Q. Doe'")
            connection.execute(
                "UPDATE run SET finished_at = CURRENT_TIMESTAMP WHERE finished_at IS NULL"
            )
            connection.commit()

        with Session(engine) as session:
            run = session.exec(select(Run).where(Run.notes == "fix a typo")).one()
            changes = [(c.operation, c.changes) for c in run.row_changes]
        assert run.kind == RunKind.MANUAL
        assert run.user == "me"
        assert len(run.id.hex) == 32
        assert run.started_at.tzinfo is UTC
        assert run.finished_at is not None
        assert changes == [(ChangeOperation.UPDATE, {"full_name": ["Jane Doe", "Jane Q. Doe"]})]


@pytest.mark.usefixtures("run_id")
class TestRowChanges:
    def test_inserts_are_recorded_with_stored_keys(self, run_id: uuid.UUID, engine: Engine) -> None:
        with Session(engine) as session:
            article, author = _article(), _author()
            session.add_all([article, author])
            session.commit()
            article_hex, author_id = article.id.hex, author.id
            assert author_id is not None
            session.add(Authorship(article_id=article.id, author_id=author_id))
            session.commit()

        assert sorted(_changes(engine)) == [
            ("article", article_hex, ChangeOperation.INSERT, {}),
            ("author", str(author_id), ChangeOperation.INSERT, {}),
            ("authorship", f"{article_hex},{author_id}", ChangeOperation.INSERT, {}),
        ]
        with Session(engine) as session:
            assert set(session.exec(select(RowChange.run_id)).all()) == {run_id}

    def test_row_id_matches_stored_key(self, engine: Engine) -> None:
        with Session(engine) as session:
            session.add(_article())
            session.commit()

        with engine.connect() as connection:
            stored = connection.exec_driver_sql(
                "SELECT 1 FROM article JOIN row_change ON row_change.row_id = article.id"
            ).all()
        assert len(stored) == 1

    def test_updates_record_old_and_new_values(self, engine: Engine) -> None:
        with Session(engine) as session:
            article = _article()
            session.add(article)
            session.commit()

            article.title = "A Title"  # same value
            session.commit()
            article.title = "New Title"
            article.url = "https://example.com"  # same value
            session.commit()
            article_hex = article.id.hex

        assert _changes(engine)[1:] == [
            ("article", article_hex, ChangeOperation.UPDATE, {"title": ["A Title", "New Title"]}),
        ]

    def test_updated_at_alone_is_not_a_change(self, engine: Engine) -> None:
        with Session(engine) as session:
            article = _article()
            session.add(article)
            session.commit()

            article.updated_at = datetime.now(UTC)
            session.commit()
            article.title = "New Title"
            article.updated_at = datetime.now(UTC)
            session.commit()

        assert [c[3] for c in _changes(engine)[1:]] == [{"title": ["A Title", "New Title"]}]

    def test_values_are_recorded_as_stored(self, engine: Engine) -> None:
        with Session(engine) as session:
            first, second = _article("REF1"), _article("REF2")
            file = ArticleFile(
                article=first,
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                url="https://example.com/a.pdf",
                path="a.pdf",
                checksum="abc",
            )
            session.add_all([first, second, file])
            session.commit()

            file.article_id = second.id
            file.created_at = datetime(2026, 2, 1, tzinfo=UTC)
            session.commit()
            first_hex, second_hex = first.id.hex, second.id.hex

        assert _changes(engine)[-1][3] == {
            "article_id": [first_hex, second_hex],
            "created_at": ["2026-01-01 00:00:00.000000", "2026-02-01 00:00:00.000000"],
        }

    def test_deletes_record_the_deleted_row(self, engine: Engine) -> None:
        with Session(engine) as session:
            author = _author()
            session.add(author)
            session.commit()
            author_id = author.id
            session.delete(author)
            session.commit()

        operation, changes = _changes(engine)[-1][2:]
        assert operation == ChangeOperation.DELETE
        assert changes["id"] == [author_id, None]
        assert changes["full_name"] == ["Jane Doe", None]
        assert set(changes) == {c.name for c in Author.__table__.columns}  # type: ignore[attr-defined]

    def test_cascaded_deletes_are_recorded(self, engine: Engine) -> None:
        with Session(engine) as session:
            article, author = _article(), _author()
            session.add_all([article, author])
            session.commit()
            assert author.id is not None
            session.add(Authorship(article_id=article.id, author_id=author.id))
            session.commit()

        with engine.begin() as connection:
            connection.exec_driver_sql("DELETE FROM author")

        deleted = {c[0] for c in _changes(engine) if c[2] == ChangeOperation.DELETE}
        assert deleted == {"author", "authorship"}

    def test_rolled_back_writes_leave_no_changes(self, engine: Engine) -> None:
        with Session(engine) as session:
            session.add(_author())
            session.flush()
            session.rollback()

        assert _changes(engine) == []

    def test_changed_at_reads_back_as_utc(self, engine: Engine) -> None:
        with Session(engine) as session:
            session.add(_author())
            session.commit()

        with Session(engine) as session:
            change = session.exec(select(RowChange)).one()
        assert change.changed_at.tzinfo is UTC
        assert datetime.now(UTC) - change.changed_at < timedelta(minutes=1)


class TestRefreshAuditTriggers:
    @pytest.mark.usefixtures("run_id")
    def test_new_tables_are_audited_after_a_refresh(self, engine: Engine) -> None:
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE note (id INTEGER PRIMARY KEY, body TEXT)")
            refresh_audit_triggers(connection)
            connection.exec_driver_sql("INSERT INTO note (body) VALUES ('hello')")

        assert _changes(engine) == [("note", "1", ChangeOperation.INSERT, {})]

    @pytest.mark.usefixtures("run_id")
    def test_triggers_dropped_by_a_table_rebuild_are_restored(self, engine: Engine) -> None:
        with engine.begin() as connection:
            operations = Operations(MigrationContext.configure(connection))
            with operations.batch_alter_table("author", recreate="always") as batch_op:
                batch_op.add_column(Column("note", String()))
            names: Sequence[str] = (
                connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type = 'trigger'")
                .scalars()
                .all()
            )
            assert "audit_author_insert" not in names, "SQLite keeps no triggers"

            refresh_audit_triggers(connection)
            connection.exec_driver_sql(INSERT_AUTHOR)
            connection.exec_driver_sql("UPDATE author SET note = 'hello'")

        assert [c[2:] for c in _changes(engine)] == [
            (ChangeOperation.INSERT, {}),
            (ChangeOperation.UPDATE, {"note": [None, "hello"]}),
        ]

    def test_refresh_is_idempotent(self, engine: Engine, db_path: Path) -> None:
        before = _triggers(db_path)
        with engine.begin() as connection:
            refresh_audit_triggers(connection)

        assert _triggers(db_path) == before
        assert "audit_article_update" in before
        assert not any("row_change_insert" in name or "alembic" in name for name in before)

    def test_without_run_tables_triggers_are_only_dropped(
        self, engine: Engine, db_path: Path
    ) -> None:
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP TABLE row_change")
            refresh_audit_triggers(connection)

        assert _triggers(db_path) == []


class TestDropAuditTriggers:
    @pytest.mark.usefixtures("run_id")
    def test_row_changes_can_be_pruned_between_drop_and_refresh(
        self, engine: Engine, db_path: Path
    ) -> None:
        with engine.begin() as connection:
            connection.exec_driver_sql(INSERT_AUTHOR)
        assert len(_changes(engine)) == 1

        with engine.begin() as connection:
            drop_audit_triggers(connection)
            connection.exec_driver_sql("DELETE FROM row_change")
            refresh_audit_triggers(connection)

        assert _changes(engine) == []
        assert "audit_row_change_delete" in _triggers(db_path)


class TestGitCommit:
    def test_names_the_checked_out_commit(self) -> None:
        head = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
        commit = git_commit()

        assert commit is not None
        assert commit.removesuffix("-dirty") == head

    def test_marks_uncommitted_changes_as_dirty(self, tmp_path: Path) -> None:
        subprocess.run(["git", "init", "-q", tmp_path], check=True)
        (tmp_path / "a").write_text("1")
        git = ["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run([*git, "add", "a"], check=True)
        subprocess.run([*git, "commit", "-q", "-m", "a"], check=True)
        clean = git_commit(tmp_path)
        (tmp_path / "a").write_text("2")

        assert clean is not None
        assert not clean.endswith("-dirty")
        assert git_commit(tmp_path) == f"{clean}-dirty"

    def test_is_none_outside_a_checkout(self, tmp_path: Path) -> None:
        assert git_commit(tmp_path) is None


class TestManualRun:
    def test_closes_the_run_when_the_block_ends(self, engine: Engine) -> None:
        with manual_run(engine, "fix a typo") as run_id, Session(engine) as session:
            session.add(_author())
            session.commit()

        with Session(engine) as session:
            run = session.get_one(Run, run_id)
            change_count = len(run.row_changes)
        assert (run.kind, run.notes, run.finish_reason) == (
            RunKind.MANUAL,
            "fix a typo",
            "finished",
        )
        assert change_count == 1

    def test_marks_the_run_failed_if_the_block_raises(self, engine: Engine) -> None:
        with pytest.raises(ValueError, match="boom"), manual_run(engine, "fix a typo"):
            raise ValueError("boom")  # noqa: EM101

        with Session(engine) as session:
            run = session.exec(select(Run)).one()
        assert run.finish_reason == "failed"
        assert run.finished_at is not None


def _finish_open_run(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE run SET finished_at = CURRENT_TIMESTAMP WHERE finished_at IS NULL"
        )


def _triggers(db_path: Path) -> list[str]:
    with closing(sqlite3.connect(db_path)) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' ORDER BY name"
        ).fetchall()
    return [row[0] for row in rows]


def _changes(engine: Engine) -> list[tuple[str, str, ChangeOperation, dict[str, Any]]]:
    with Session(engine) as session:
        rows = session.exec(select(RowChange).order_by(RowChange.id)).all()  # type: ignore[arg-type]
    return [(r.table_name, r.row_id, r.operation, r.changes) for r in rows]


def _article(reference: str = "REF1") -> Article:
    return Article(
        title="A Title", url="https://example.com", repository="repo", reference=reference
    )


def _author() -> Author:
    return Author(full_name="Jane Doe", canonical_name="doe jane")
