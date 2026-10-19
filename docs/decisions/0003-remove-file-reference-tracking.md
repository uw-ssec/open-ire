---
# prettier-ignore
status: accepted
date: 2026-10-06
decision-makers: Richard Boyechko
consulted: Liz Bedford
---

# Remove File Reference Tracking

## Context and Problem Statement

Some publications stored on the US government repositories like EPA include
dataset files in addition to the PDF file with the publication itself. Such
dataset files are too big to download and store like we do the PDFs, so we opted
to save only the file metadata, including URLs and reported sizes, without
downloading the files. The `ArticleFileReference` table was intended to store
this metadata, akin to the `ArticleFile` table that tracked the PDF files.
However, only a few publications in the EPA repository had the dataset files,
none of the project workflows involves tracking their sizes, no reference file
metadata was ever collected, and none of the other repository spiders even tried
to track such information. Keeping this unused feature required maintaining
additional code and an extra database table.

## Considered Options

- Remove the feature: Fewer database tables, less code to maintain, but drops
  any already scraped data, and removes potentially useful functionality.
- Keep the feature: Retains supplementary-file metadata support for future
  workflows, but adds maintenance without a current consumer.

## Decision Outcome

Remove supplementary-file reference tracking while retaining downloaded
publication PDF file tracking. This reduces database and crawler maintenance.
The migration discards existing reference records; downgrading restores only the
empty table, so recovering those records requires a backup.

## More Information

The previous implementation remains available in Git history
([commit 546a2b8](https://github.com/uw-ssec/open-ire/commit/546a2b84d7a8fa49a652f201453a408195953321)).
The removal was implemented in
[PR #158](https://github.com/uw-ssec/open-ire/pull/158). Revisit this decision
if a concrete project workflow requires supplementary-file metadata.
