import marimo

__generated_with = "0.21.1"
app = marimo.App(width="medium")


@app.cell
def _():
    import datetime
    import time
    from pathlib import Path

    import marimo as mo
    import pandas as pd
    import requests

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
        Path,
        check_url,
        datetime,
        mo,
        pd,
        time,
    )


@app.cell
def _(mo):
    mo.md(
        "## Verify Unavailable Articles\n\nRe-check URLs from an unavailable-articles CSV and filter out any that have recovered."
    )
    file_input = mo.ui.text(
        label="CSV file path",
        value="output/unavailable_articles_2026-03-03.csv",
        full_width=True,
    )
    mo.output.replace(
        mo.vstack(
            [
                mo.md(
                    "## Verify Unavailable Articles\n\n"
                    "Re-check URLs from an unavailable-articles CSV and "
                    "filter out any that have recovered."
                ),
                file_input,
            ]
        )
    )
    return (file_input,)


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
    return (recovered_df, still_erroring_df)


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
