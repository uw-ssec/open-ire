---
# prettier-ignore
status: accepted
date: 2026-11-13
decision-makers: Richard Boyechko
---

# Record Run Provenance with Database Triggers

## Context and Problem Statement

The database does not record who changed it, when, or why, nor which crawl
created or changed a given row. The run logs hold some of this for crawls, but
they are files kept apart from the data and cannot be queried alongside it.

Crawls are not the only writers. Data is also fixed by hand in a database
browser, changed by data migrations, and edited from notebooks. These are the
changes most in need of a stated reason, and the hardest to reconstruct later.
We want every change, from any client, to be attributed to a run that says who
made it and why.

## Considered Options

- **Run stamps on every table** (`created_by_run_id`, `updated_by_run_id`):
  simple to query, but keeps only the latest writer, changes every table, and
  must be set at each write site.
- **An ORM flush hook** writing a row-change table while a crawl's run is
  active: needs no changes at write sites, but sees only Open IRE's ORM writes,
  missing database browsers, data migrations, Core bulk statements, and cascaded
  deletes.
- **A versioning library** (SQLAlchemy-Continuum or sqlalchemy-history): full
  row history, but also ORM-only, with a shadow table per model that every
  migration must cover.
- **Temporary triggers on each Open IRE connection:** catch SQLAlchemy Core
  statements and cascades too, but still nothing from other clients.
- **Permanent triggers reading a SQL function Open IRE registers:** other
  clients fail with "no such function" on every write, with no way to comply.
- **Permanent triggers that require an open run in the `run` table:** every
  client's writes are recorded, and a client without an open run is told how to
  open one.

## Decision Outcome

Add a `run` table and a `row_change` table, and audit triggers on every other
table that require an open run. A run is open until its `finished_at` is set,
and only one run can be open at a time. Both tables are append-only, enforced by
triggers: an open run can only be closed, a closed run can't be changed or
deleted, and row changes can't be changed or deleted. To say more about a past
run, open a new one and say it in its notes.

Each trigger rejects a write when no run is open, with a message saying how to
open one. Otherwise it records the write as a `row_change` of the open run:
updates as each changed column's old and new values, leaving out `updated_at`,
which the pipelines bump whenever a crawl sees a row again; deletes as the
deleted row's values; inserts as the new row's key alone. Values are kept as
SQLite stores them.

Runs are opened by whatever is about to write:

- A crawl opens one through the `RunRecorder` extension, and closes it with
  Scrapy's close reason and stats. If it cannot open one, it closes the spider.
- An Alembic command that changes the revision opens a migration run, with any
  `-x notes="..."` added to its notes.
- A notebook or script uses `manual_run()`.
- A person in a database browser inserts a `run` row, giving their name and
  notes. Notes are required for manual runs, and the other columns have
  defaults.

The triggers are generated from the database's actual schema after every
migration, so they cover new tables and columns, and are restored after Alembic
rebuilds a table (which drops its triggers).

### Consequences

- Good, because every change, from any client, is attributed to a run with a
  user and, for manual changes, a stated reason.
- Good, because data migrations are recorded without anyone remembering to.
- Good, because deleted rows' contents are kept, and old values come with no
  extra queries.
- Good, because existing tables and write code are unchanged.
- Bad, because a run is open for the whole database: a database browser edit
  made during a crawl is attributed to the crawl. Don't edit by hand while a
  crawl or migration is running.
- Bad, because a run left open (by a crash, or a person who forgets to close it)
  blocks the next crawl or migration until someone closes it. The error names
  the run and how to close it.
- Bad, because every write adds a row, so the database grows with activity.
- Bad, because the triggers are SQL generated from strings, harder to read and
  debug than Python, and specific to SQLite.
- Bad, because a table rebuilt partway through an upgrade is not audited until
  the upgrade ends, so a later migration's writes to it in the same upgrade are
  not recorded.
- Bad, because in-memory databases, built without migrations, have no triggers,
  and anyone with write access can drop the triggers. The record keeps honest
  people honest; it does not stop a determined one.
- Bad, because existing databases need a manual upgrade (see
  [ADR-0002](0002-require-manual-database-migrations.md)), and rows written
  before it have no history.

## More Information

- [Runs and audit triggers](../../src/open_ire/provenance.py)
- [Migration runs](../../src/open_ire/alembic/env.py)
- [Crawl runs](../../src/open_ire/extensions/run_recorder.py)
- "Recording Database Changes" in [CONTRIBUTING.md](../../CONTRIBUTING.md)

Revisit this decision if writers need to work concurrently with separate
attribution, or if the database moves to a server such as PostgreSQL, where
triggers can read per-session settings and so tell concurrent writers apart.
