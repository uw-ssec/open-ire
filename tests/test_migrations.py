"""Exercise migrations and check for schema drift using Alembic autogeneration."""

from pathlib import Path

import pytest
from alembic import command

from open_ire.db import get_alembic_config


def test_migrations_round_trip_matches_models(tmp_path: Path) -> None:
    """Check fresh and round-trip schemas against models on temporary SQLite."""
    cfg = get_alembic_config(f"sqlite:///{tmp_path / 'roundtrip.db'}")

    command.upgrade(cfg, "head")
    command.check(cfg)

    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    command.check(cfg)


def test_cli_fallback_rejects_missing_database_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mistyped OPEN_IRE_DATABASE_FILE raises instead of creating an empty database."""
    missing = tmp_path / "typo.db"
    monkeypatch.setattr("open_ire.settings.base.OPEN_IRE_DATABASE_FILE", str(missing))
    cfg = get_alembic_config("unused")
    del cfg.attributes["sqlalchemy.url"]  # Mimic the CLI, which passes no URL attribute.

    with pytest.raises(FileNotFoundError, match=r"typo\.db"):
        command.current(cfg)

    assert not missing.exists()
