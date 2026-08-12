# OSTI relevance audit: why non-UW articles enter the corpus

Audit date: 2026-08-11 Data audited: `open_ire_2026-06-16.db` (dev/test run),
23,258 `repository='osti'` rows.

## Summary

An estimated **76% of the OSTI corpus (~17,700 of 23,258 rows) has no University
of Washington connection at all**. The cause is not a bad keyword — it is that
OSTI's `q` parameter searches the **full text of the PDF**, so any report that
merely _mentions_ "University of Washington" anywhere in its body (references,
acknowledgements, an attendee list) is returned as a hit. Nothing downstream
re-checks affiliation.

## The reported example

`https://www.osti.gov/biblio/10158090` — _"Fish Passage Center; Columbia Basin
Fish and Wildlife Authority, 1993 Annual Report"_, research org _Columbia Basin
Fish and Wildlife Authority_, sponsor _US Bonneville Power Administration_.

The string "washington" appears **nowhere** in its metadata or on its OSTI
biblio page. Probing the API with field-scoped queries isolates the match
exactly:

| query                                                          | hits  |
| -------------------------------------------------------------- | ----- |
| `osti_id:10158090 AND "university of washington"`              | 1     |
| `osti_id:10158090 AND fulltext:"university of washington"`     | **1** |
| `osti_id:10158090 AND title:"university of washington"`        | 0     |
| `osti_id:10158090 AND description:"university of washington"`  | 0     |
| `osti_id:10158090 AND research_org:"university of washington"` | 0     |
| `osti_id:10158090 AND author:"university of washington"`       | 0     |
| `osti_id:10158090 AND` (each of our other 6 terms)             | 0     |

It matched the **full-text index only**, on the term
`"university of washington"`.

## Root cause

`OstiSpider._build_page_request` sends the term as a bare relevance query:

```text
params = {"q": f'"{term}"', "has_fulltext": "true", "rows": ..., "page": ...}
```

`q` is OSTI's site-wide search: title, abstract, authors, orgs **and indexed
full text**. Quoting only constrains the phrase; it does not constrain _which
field_ the phrase is in. So `q="university of washington"` returns 23,801
records site-wide — and our DB holds 23,258 of them, i.e. essentially that
single term's entire result set.

There is no affiliation check anywhere downstream. `ITEM_PIPELINES` contains
duplicate/DOI/file/SharePoint/SQL stages only — nothing filters on institution.

Contrast the two spiders that do this correctly:

- `openalex.py:377` — `filter=affiliations.institution.id:i201448701`
  (structured ID)
- `wos.py:92` — `OG=("University of Washington")` (organization-enhanced field)

`osti`, `eric`, and `epa` all use unscoped free-text search instead.

## Measured impact

200 randomly sampled OSTI rows that carry no UW term in stored metadata were
re-fetched from the OSTI API and checked against the **complete raw record**:

| outcome                                                           | count   | share     |
| ----------------------------------------------------------------- | ------- | --------- |
| UW present in raw record (author affiliation the spider stripped) | 36      | 18.0%     |
| **No UW anywhere in the record → full-text-only match**           | **164** | **82.0%** |

95% CI on the full-text-only rate: 76.7%–87.3%.

Extrapolated over the 21,642 rows with no UW term in stored metadata: **~17,700
false positives (CI 16,600–18,900), or 76% of the OSTI corpus (CI 71%–81%).**

Corroboration from the other direction: only 1,880 rows have a research org that
is plausibly UW Seattle, and a field-scoped API query for UW research orgs
returns 1,895 records — both roughly 8% of what we actually collected.

## Secondary findings

1. **Author affiliations are discarded.** `_extract_authors` strips the
   bracketed institution via `_AFFILIATION_RE`. That is the single best
   per-article UW signal in an OSTI record, and 18% of the sample had UW _only_
   there. It is thrown away before the item is stored, so relevance cannot be
   re-checked offline.

2. **Stopwords are dropped in phrase matching.** `q="washington of university"`
   returns 8,553 hits, so "of" is not indexed — `"university of washington"`
   effectively matches the phrase _university washington_. This pulls in
   "Washington University" (St. Louis) and "George Washington University". Real
   but minor here: 19 and 9 rows respectively.

3. **`"harborview injury prevention and research center"` matches 0 records** on
   OSTI — it is dead weight in the term list for this spider.

4. **The matched search term is not recorded** on the item, so a false positive
   cannot be traced back to the term that produced it without re-querying the
   API.

5. **Not OSTI-specific.** Share of rows with any exact UW term in stored
   metadata:

   | repo       | rows   | exact UW term |
   | ---------- | ------ | ------------- |
   | noaa       | 23,550 | 1.6%          |
   | cdc_stacks | 10,279 | 1.2%          |
   | osti       | 23,258 | 7.0%          |
   | eric       | 3,381  | 10.1%         |
   | epa        | 70     | 61.4%         |

   `noaa` and `cdc_stacks` look worse than OSTI. They have not been verified
   against their source APIs the way OSTI was here, so this is a flag for the
   same audit, not a measured false-positive rate.

## Validated fix

OSTI v1 supports field-scoped search. This query was tested against 395 results
across 4 pages spread through the result set:

```
research_org:(("university of washington" OR "univ. of washington" OR "washington univ") AND seattle)
```

- 1,895 records with `has_fulltext=true`
- **99.7% precision** (394/395 had a UW Seattle research org), vs ~22% today
- the ` AND seattle` clause is what excludes Washington University in St. Louis

Recommended, in order:

1. Scope the OSTI query to `research_org:` instead of bare `q`. Use per-spider
   terms — the shared `OPEN_IRE_SEARCH_TERMS` list mixes institution names with
   email domains (`uw.edu`, `washington.edu`), which are full-text-only signals
   and cannot be field-scoped.
2. Stop stripping author affiliations in `_extract_authors`; keep them in
   `extra` so relevance stays auditable after the crawl.
3. Add an affiliation-check pipeline that drops items with no UW evidence in any
   structured field, so a permissive source query cannot silently flood the
   corpus.
4. Record the matching search term on each item for traceability.
5. Re-run the audit for `noaa` and `cdc_stacks`.
