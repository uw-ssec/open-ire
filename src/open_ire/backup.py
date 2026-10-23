import gzip
import re
import shutil
import sqlite3
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

# Backups are named by the pipeline rather than after the local files, so
# pruning depends only on this format. Changing it orphans existing backups.
BACKUP_NAME = "open_ire__{run_at:%Y-%m-%d_%H-%M-%S}.{extension}"
BACKUP_NAME_PATTERN = re.compile(
    r"open_ire__(?P<date>\d{4}-\d{2}-\d{2})_\d{2}-\d{2}-\d{2}\.(?:db\.gz|log)"
)


def create_snapshot(db_path: Path, dest_dir: Path) -> Path:
    """Write a compacted, gzipped copy of the database into ``dest_dir``.

    ``VACUUM INTO`` reads through a read-only connection and produces a
    transactionally consistent copy, so the live database is never held
    open for writing and free pages are dropped from the result.

    Parameters
    ----------
    db_path
        Path to the live SQLite database.
    dest_dir
        Directory to write the snapshot into; must already exist.

    Returns
    -------
    Path to the gzipped snapshot.
    """
    vacuumed = dest_dir / db_path.name
    connection = sqlite3.connect(f"{db_path.absolute().as_uri()}?mode=ro", uri=True)
    try:
        connection.execute("VACUUM INTO ?", (str(vacuumed),))
    finally:
        connection.close()

    compressed = dest_dir / f"{vacuumed.name}.gz"
    with vacuumed.open("rb") as raw, gzip.open(compressed, "wb", compresslevel=6) as gz:
        shutil.copyfileobj(raw, gz)
    vacuumed.unlink()

    return compressed


def stale_backups(names: Iterable[str], days_kept: int) -> list[str]:
    """Return the backups dated outside the most recent ``days_kept`` days.

    Only names matching ``BACKUP_NAME_PATTERN`` are considered, so anything
    else is never stale. A day's snapshots and logs are kept or dropped
    together. ``days_kept`` must be positive; zero would mark every backup
    stale.
    """
    backups_by_date: dict[str, list[str]] = defaultdict(list)
    for name in names:
        if match := BACKUP_NAME_PATTERN.fullmatch(name):
            backups_by_date[match["date"]].append(name)

    stale_dates = sorted(backups_by_date, reverse=True)[days_kept:]

    return [name for date in stale_dates for name in backups_by_date[date]]
