import marimo

__generated_with = "0.21.1"
app = marimo.App(width="medium")


@app.cell
def _():
    import asyncio
    import datetime
    import sqlite3
    import time
    from pathlib import Path
    from urllib.parse import quote_plus, urljoin

    import marimo as mo
    import pandas as pd
    import requests
    from lxml import html as lxml_html
    from playwright.async_api import async_playwright
    from rapidfuzz import fuzz

    FIELDNAMES = [
        "article_id",
        "repository",
        "reference",
        "kind",
        "url",
        "status_code",
        "error",
        "request_method",
        "checked_at",
    ]
    REQUEST_TIMEOUT = 15
    DELAY_BETWEEN_REQUESTS = 0.5
    NOAA_BASE = "https://repository.library.noaa.gov"

    def check_url(url: str, *, timeout: int = REQUEST_TIMEOUT) -> int | None:
        """Return the HTTP status code for *url*, or None on connection error."""
        try:
            resp = requests.head(url, timeout=timeout, allow_redirects=True)
            # Some servers reject HEAD; fall back to GET.
            if resp.status_code == 405:
                resp = requests.get(url, timeout=timeout, allow_redirects=True, stream=True)
            return resp.status_code
        except requests.RequestException:
            return None

    return (
        DELAY_BETWEEN_REQUESTS,
        FIELDNAMES,
        NOAA_BASE,
        Path,
        async_playwright,
        asyncio,
        check_url,
        datetime,
        fuzz,
        lxml_html,
        mo,
        pd,
        quote_plus,
        sqlite3,
        time,
        urljoin,
    )


@app.cell
def _(mo):
    file_input = mo.ui.text(
        label="CSV file path",
        value="output/unavailable_articles_2026-03-03.csv",
        full_width=True,
    )
    mo.output.replace(
        mo.vstack(
            [
                mo.md(
                    "# Verify Unavailable Articles\n\n"
                    "Re-check URLs from an unavailable-articles CSV, filter out "
                    "recovered URLs, and search the NOAA repository for "
                    "relocated articles."
                ),
                file_input,
            ]
        )
    )
    return (file_input,)


@app.cell
def _(mo):
    mo.md("""
    ### Load CSV
    """)
    return


@app.cell
def _(FIELDNAMES, Path, file_input, mo, pd):
    csv_path = Path(file_input.value)
    mo.stop(
        not csv_path.exists(),
        mo.callout(mo.md(f"**File not found:** `{csv_path}`"), kind="danger"),
    )
    articles_df = pd.read_csv(csv_path)
    missing = set(FIELDNAMES) - set(articles_df.columns)
    mo.stop(
        len(missing) > 0,
        mo.callout(
            mo.md(f"**Missing columns:** {', '.join(sorted(missing))}"),
            kind="danger",
        ),
    )
    mo.output.replace(
        mo.vstack(
            [
                mo.md(f"Loaded **{len(articles_df)}** URLs from `{csv_path.name}`"),
                mo.ui.table(articles_df),
            ]
        )
    )
    return (articles_df,)


@app.cell
def _(mo):
    mo.md("""
    ## URL Verification
    """)
    return


@app.cell
def _(DELAY_BETWEEN_REQUESTS, articles_df, mo):
    estimated_seconds = int(len(articles_df) * (DELAY_BETWEEN_REQUESTS + 0.1))
    estimated_min = estimated_seconds // 60
    estimated_sec = estimated_seconds % 60
    run_button = mo.ui.run_button(
        label=f"Check {len(articles_df)} URLs (~{estimated_min}m {estimated_sec}s)",
    )
    mo.output.replace(run_button)
    return (run_button,)


@app.cell
def _(
    DELAY_BETWEEN_REQUESTS,
    FIELDNAMES,
    articles_df,
    check_url,
    datetime,
    mo,
    pd,
    run_button,
    time,
):
    mo.stop(
        not run_button.value,
        mo.md("*Press the button above to start checking.*"),
    )

    now = datetime.datetime.now(datetime.UTC).isoformat()
    still_erroring = []
    recovered = []

    with mo.status.progress_bar(total=len(articles_df)) as bar:
        for i, row in articles_df.iterrows():
            url = row["url"]
            status = check_url(url)

            entry = row.to_dict()
            if status is None or status >= 400:
                entry["status_code"] = str(status) if status is not None else ""
                entry["error"] = f"HTTP {status}" if status is not None else "connection_error"
                entry["request_method"] = "GET"
                entry["checked_at"] = now
                still_erroring.append(entry)
            else:
                entry["new_status"] = str(status)
                recovered.append(entry)

            bar.update()
            if i < len(articles_df) - 1:
                time.sleep(DELAY_BETWEEN_REQUESTS)

    still_erroring_df = pd.DataFrame(still_erroring, columns=FIELDNAMES)
    recovered_df = pd.DataFrame(recovered)
    return recovered_df, still_erroring_df


@app.cell
def _(mo):
    mo.md("""
    ### Results
    """)
    return


@app.cell
def _(mo, recovered_df, still_erroring_df):
    mo.hstack(
        [
            mo.stat(value=f"{len(recovered_df):,}", label="Recovered"),
            mo.stat(value=f"{len(still_erroring_df):,}", label="Still erroring"),
        ],
        justify="start",
        gap=1,
    )
    return


@app.cell
def _(mo, recovered_df):
    mo.stop(len(recovered_df) == 0)
    mo.output.replace(
        mo.vstack(
            [
                mo.md("### Recovered URLs"),
                mo.ui.table(recovered_df[["url", "repository", "reference"]]),
            ]
        )
    )
    return


@app.cell
def _(mo, still_erroring_df):
    mo.output.replace(
        mo.vstack(
            [
                mo.md("### Still Erroring"),
                mo.ui.table(still_erroring_df),
            ]
        )
    )
    return


@app.cell
def _(mo):
    mo.md("""
    ## Search for Relocated Articles
    """)
    return


@app.cell
def _(mo):
    mo.md("""
    ### Title Lookup
    """)
    return


@app.cell
def _(NOAA_BASE, fuzz, lxml_html, pd, quote_plus, sqlite3, urljoin):
    def lookup_titles(erroring_df: pd.DataFrame, db_path: str) -> pd.DataFrame:
        """Join still-erroring NOAA articles with their titles from the DB."""
        noaa = erroring_df[
            (erroring_df["repository"] == "noaa") & (erroring_df["kind"] == "article_metadata")
        ].copy()
        if len(noaa) == 0:
            return pd.DataFrame()

        noaa["article_id_hex"] = noaa["article_id"].str.replace("-", "", regex=False)
        hex_ids = noaa["article_id_hex"].tolist()
        placeholders = ",".join("?" * len(hex_ids))

        con = sqlite3.connect(str(db_path))
        try:
            titles = pd.read_sql_query(
                f"SELECT id, title FROM article WHERE id IN ({placeholders})",
                con,
                params=hex_ids,
            )
        finally:
            con.close()

        return noaa.merge(titles, left_on="article_id_hex", right_on="id", suffixes=("", "_db"))

    async def search_noaa(page, title: str, max_results: int = 5) -> list[dict]:
        """Search the NOAA repository for articles matching *title*."""
        url = f"{NOAA_BASE}/gsearch?terms={quote_plus(title)}&maxResults={max_results}"
        await page.goto(url)
        await page.wait_for_load_state("networkidle")
        tree = lxml_html.fromstring(await page.content())
        candidates = []
        for node in tree.xpath("//div[@class='object-title']/a"):
            result_title = (node.text or "").strip()
            href = node.get("href", "")
            if result_title and href:
                ref = href.rstrip("/").split("/")[-1] if "/view/noaa/" in href else ""
                candidates.append(
                    {
                        "matched_title": result_title,
                        "new_url": urljoin(NOAA_BASE, href),
                        "new_reference": ref,
                    }
                )
        return candidates

    def best_match(title: str, candidates: list[dict], threshold: int) -> dict | None:
        """Return the best fuzzy match above *threshold*, or None."""
        if not candidates:
            return None
        best = max(
            candidates,
            key=lambda c: fuzz.token_sort_ratio(title, c["matched_title"]),
        )
        score = fuzz.token_sort_ratio(title, best["matched_title"])
        if score >= threshold:
            return {**best, "similarity": score}

        return None

    return best_match, lookup_titles, search_noaa


@app.cell
def _(lookup_titles, mo, still_erroring_df):
    _db_path = mo.notebook_dir() / ".." / "dbs" / "open_ire.db"
    mo.stop(
        not _db_path.exists(),
        mo.callout(mo.md(f"**Database not found:** `{_db_path}`"), kind="danger"),
    )

    noaa_with_titles = lookup_titles(still_erroring_df, _db_path)
    mo.stop(
        len(noaa_with_titles) == 0,
        mo.md("*No NOAA article metadata URLs in the still-erroring list.*"),
    )
    mo.output.replace(
        mo.md(f"Found **{len(noaa_with_titles)}** NOAA articles with titles to search.")
    )
    return (noaa_with_titles,)


@app.cell
def _(mo):
    mo.md("""
    ### Repository Search
    """)
    return


@app.cell
def _(DELAY_BETWEEN_REQUESTS, mo, noaa_with_titles):
    _n = len(noaa_with_titles)
    _est_sec = int(_n * (DELAY_BETWEEN_REQUESTS + 1.0))
    _est_min, _est_s = divmod(_est_sec, 60)

    similarity_threshold = mo.ui.slider(
        start=50, stop=100, value=95, step=5, label="Minimum similarity (%)"
    )
    search_button = mo.ui.run_button(
        label=f"Search NOAA for {_n} articles (~{_est_min}m {_est_s}s)"
    )
    mo.output.replace(mo.vstack([similarity_threshold, search_button]))
    return search_button, similarity_threshold


@app.cell
async def _(
    DELAY_BETWEEN_REQUESTS,
    async_playwright,
    asyncio,
    best_match,
    mo,
    noaa_with_titles,
    pd,
    search_button,
    search_noaa,
    similarity_threshold,
):
    mo.stop(
        not search_button.value,
        mo.md("*Press the button above to start searching.*"),
    )

    _threshold = similarity_threshold.value
    _results = []

    _playwright = await async_playwright().start()
    _browser = await _playwright.chromium.launch(headless=False)
    _page = await _browser.new_page()

    try:
        with mo.status.progress_bar(total=len(noaa_with_titles)) as _bar:
            for _, _row in noaa_with_titles.iterrows():
                try:
                    _candidates = await search_noaa(_page, _row["title"])
                except Exception:
                    _candidates = []

                _match = best_match(_row["title"], _candidates, _threshold)
                if _match:
                    _results.append(
                        {
                            "article_id": _row["article_id"],
                            "original_title": _row["title"],
                            "original_reference": _row["reference"],
                            "original_url": _row["url"],
                            **_match,
                        }
                    )

                _bar.update()
                await asyncio.sleep(DELAY_BETWEEN_REQUESTS)
    finally:
        await _page.close()
        await _browser.close()
        await _playwright.stop()

    search_results_df = pd.DataFrame(_results)
    return (search_results_df,)


@app.cell
def _(mo):
    mo.md("""
    ### Search Results
    """)
    return


@app.cell
def _(datetime, mo, search_results_df):
    if len(search_results_df) == 0:
        mo.output.replace(
            mo.callout(
                mo.md("No relocated articles found above the similarity threshold."),
                kind="info",
            )
        )
    else:
        _csv_bytes = search_results_df.to_csv(index=False).encode()
        _filename = f"relocated_articles_{datetime.date.today().isoformat()}.csv"
        mo.output.replace(
            mo.vstack(
                [
                    mo.md(
                        f"### Relocated Articles\n\n"
                        f"Found **{len(search_results_df)}** potential matches."
                    ),
                    mo.ui.table(search_results_df, selection=None),
                    mo.download(
                        data=_csv_bytes,
                        filename=_filename,
                        label="Download relocated articles CSV",
                    ),
                ]
            )
        )
    return


@app.cell
def _(datetime, mo, noaa_with_titles, search_results_df):
    _relocated_ids = set(search_results_df["article_id"]) if len(search_results_df) > 0 else set()
    _not_relocated = noaa_with_titles[~noaa_with_titles["article_id"].isin(_relocated_ids)][
        ["article_id", "title", "reference", "url"]
    ]

    if len(_not_relocated) == 0:
        mo.output.replace(
            mo.callout(
                mo.md("All searched articles were successfully relocated."),
                kind="info",
            )
        )
    else:
        _csv_bytes = _not_relocated.to_csv(index=False).encode()
        _filename = f"not_relocated_articles_{datetime.date.today().isoformat()}.csv"
        mo.output.replace(
            mo.vstack(
                [
                    mo.md(
                        f"### Not Relocated\n\n"
                        f"**{len(_not_relocated)}** articles could not be found "
                        f"in the NOAA repository."
                    ),
                    mo.ui.table(_not_relocated.reset_index(drop=True), selection=None),
                    mo.download(
                        data=_csv_bytes,
                        filename=_filename,
                        label="Download not-relocated articles CSV",
                    ),
                ]
            )
        )
    return


@app.cell
def _(mo):
    mo.md("""
    ## Export
    """)
    return


@app.cell
def _(FIELDNAMES, datetime, mo, still_erroring_df):
    _csv_bytes = still_erroring_df[FIELDNAMES].to_csv(index=False).encode()
    _filename = f"unavailable_articles_{datetime.date.today().isoformat()}.csv"
    mo.output.replace(
        mo.vstack(
            [
                mo.md(
                    f"Download the filtered CSV containing **{len(still_erroring_df)}** "
                    "erroring URLs."
                ),
                mo.download(data=_csv_bytes, filename=_filename, label="Download CSV"),
            ]
        )
    )
    return


if __name__ == "__main__":
    app.run()
