"""Exercise migrations and check for schema drift using Alembic autogeneration."""

from pathlib import Path

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
