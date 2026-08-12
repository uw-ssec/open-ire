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

## Options considered

OSTI v1 supports field-scoped search, so the obvious fix is to narrow the query:

```
research_org:(("university of washington" OR "univ. of washington" OR "washington univ") AND seattle)
```

Tested against 395 results across 4 pages, that returns 1,895 records at **99.7%
precision** (394/395), with ` AND seattle` excluding Washington University in
St. Louis.

It was **rejected as the sole fix** because it costs too much recall. OSTI's
`author:` index does not cover affiliation text —
`author:"university of washington"` returns 0 records — so a
`research_org:`-only query cannot see the articles whose UW connection is
recorded in an author's affiliation. On live data those are **44% of all
genuinely-UW records** (85 of 193 in one sample).

## Implemented fix

Keep the broad query for recall, and verify affiliation per record for
precision. Field-scoped querying stays available as a cross-check, not as the
filter.

1. **`open_ire/affiliation.py`** — `is_uw_affiliation` recognises UW in a single
   institution string and rejects the other Washington-named institutions (St.
   Louis, George Washington, Washington State, Western/Eastern/Central
   Washington) and Washington, DC agencies. `split_institutions` unpacks OSTI's
   semicolon-separated multi-institution fields so one co-author's disqualifying
   city cannot suppress a genuine UW match in the same field.

2. **`OstiSpider` gates every record** on its own structured affiliations —
   `research_orgs`, `sponsor_orgs`, and the affiliation bracketed into each
   author entry. A record with no UW among them is dropped before it reaches any
   pipeline, so no PDF is downloaded and nothing is uploaded to SharePoint for
   it. `OPEN_IRE_REQUIRE_UW_AFFILIATION=False` disables the gate to re-measure
   the unfiltered result.

3. **The evidence is preserved.** `extra["uw_affiliations"]` records the
   affiliation strings that justified collecting the article and
   `extra["search_term"]` the term that found it, so relevance stays auditable
   after the crawl. Author names are still stored without affiliation text, as
   `ParsedAuthor` requires.

### Measured on live data

Running the real spider over 600 live records from
`q="university of washington"`:

|                                         |                  |
| --------------------------------------- | ---------------- |
| kept                                    | 176 (29.3%)      |
| dropped                                 | 424 (70.7%)      |
| kept via `research_orgs`/`sponsor_orgs` | 101              |
| kept via **author affiliation only**    | 75 (43% of kept) |
| kept records with no recorded evidence  | 0                |
| kept evidence strings that are not UW   | 0                |

The reported false positive, `biblio/10158090`, is dropped. Cross-checked
against the field-scoped `research_org:` query, the gate keeps **100%** of its
300 sampled results — it discards nothing that a precision-first query would
have kept.

## Still open

1. Re-run the audit for `noaa` and `cdc_stacks`, which look worse than OSTI.
2. The existing corpus is not retro-filtered. The ~17,700 already-collected
   false positives need a separate cleanup pass; `extra["uw_affiliations"]` is
   only populated for articles collected after this change.
3. `uw.edu` and `washington.edu` remain in `OPEN_IRE_SEARCH_TERMS`. For OSTI
   they can only ever match full text, so they now cost crawl time without
   contributing articles — worth a per-spider term list later.
