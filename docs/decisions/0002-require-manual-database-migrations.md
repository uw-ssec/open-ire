---
status: accepted
date: 2026-09-25
decision-makers: Richard Boyechko
---

# Require Manual Database Migrations

## Context and Problem Statement

Automatically migrating an existing database when a crawl starts can rewrite
stored data before an operator has reviewed the changes or made a backup. The
UTC timestamp migration in
[ADR-0001](0001-store-timestamps-as-timezone-aware-utc.md) exposed this risk: it
assumes a source timezone and, although a downgrade exists, timestamps around
daylight-saving transitions may not round-trip exactly. Starting a crawl should
not implicitly authorize such changes.

## Considered Options

- **Manual migrations:** give an operator a chance to review assumptions and
  back up the affected database, but require a separate step before crawling
  after an upgrade.
- **Automatic migrations at crawl startup:** keep databases current without
  operator intervention, but may change existing data irreversibly before anyone
  has checked that the migration is appropriate for that database.

## Decision Outcome

Require explicit, manual migrations for existing databases. The operator is
responsible for selecting the target database, reviewing the migration's
assumptions, and backing it up before applying changes. This gives someone
familiar with the data control over when it is transformed; it does not
guarantee that the migration is reversible.

Database initialization requires an existing database to be at the revision
expected by the code and rejects a mismatch instead of upgrading it. An
unversioned database that already contains tables also needs operator review. A
new or empty database may still be initialized automatically because it has no
existing records to preserve.

### Consequences

- Good, because starting a crawl no longer triggers an unreviewed migration of
  existing data.
- Good, because backups provide a recovery path when a downgrade cannot restore
  the original values.
- Bad, because deploying code with a new migration requires a separate upgrade
  step before affected crawls can run.
- Bad, because operators must identify the correct database and understand any
  data assumptions; manual execution alone does not prevent mistakes.

## More Information

- [Database migration procedure](../../CONTRIBUTING.md#database-migrations)
- [Shared database initialization](../../src/open_ire/db.py) and its
  [tests](../../tests/test_db.py)

Revisit this decision if deployments gain a migration workflow that verifies the
target database, creates a recoverable backup, and requires explicit approval
before changing existing data.
