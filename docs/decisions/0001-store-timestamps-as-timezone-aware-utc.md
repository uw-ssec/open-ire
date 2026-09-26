---
status: accepted
date: 2026-09-24
decision-makers: Richard Boyechko
informed: Vedant Somani
---

# Store Timestamps as Timezone-Aware UTC

## Context and Problem Statement

Timestamps such as `created_at` defaulted to `datetime.now()`: the crawl
machine's local time, without a recorded timezone. Crawls can run in different
timezones, so we need a shared convention.

SQLModel 0.0.45 now requires timezone-aware values when writing ordinary
`datetime` fields and normalizes them to UTC. Our SQLite mapping stores these
without an offset and restores UTC on read. Existing local timestamps therefore
need conversion before adopting the new mapping.

## Considered Options

1. **UTC:** use SQLModel's default and migrate existing local timestamps.
2. **Naive local time:** use `NaiveDatetime` to keep the old behavior, leaving
   each timestamp's timezone implicit.
3. **Local time with an offset:** preserve readable local values with a custom
   mapping, but normalize them for chronological sorting across offsets.

## Decision Outcome

Use timezone-aware UTC for stored timestamps. This makes values from different
crawls directly comparable and sortable with the existing mapping, and follows
SQLModel's default.

For ambiguous or nonexistent times at daylight-saving transitions, the migration
uses the offset before the transition and logs a warning. We accept that
uncertainty for these noncritical timestamps rather than stop the migration.

### Consequences

- Good, because timestamps from different crawls can be compared and sorted
  without accounting for each machine's timezone.
- Good, because the mapping rejects naive datetime values before they reach the
  database, catching missing timezone information on write.
- Bad, because displaying timestamps in local time requires conversion from UTC.
- Bad, because existing timestamps need a data migration, and missing timezone
  information makes some conversions uncertain.
- Bad, because downgrading removes timezone information again; values around
  daylight-saving transitions are not guaranteed to round-trip.

[Migration `9843ce2b584f`](../../src/open_ire/migrations/versions/9843ce2b584f_aware_datetime.py)
assumes existing timestamps are `America/Los_Angeles` local time.

## More Information

- [SQLModel 0.0.45 release notes](https://github.com/fastapi/sqlmodel/releases/tag/0.0.45)
- [SQLModel documentation on datetimes and timezones](https://sqlmodel.tiangolo.com/advanced/datetime/)
