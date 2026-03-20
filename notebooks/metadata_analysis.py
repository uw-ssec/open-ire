import marimo

__generated_with = "0.21.1"
app = marimo.App(width="medium")


@app.cell
def _():
    import json
    import sqlite3
    from pathlib import Path

    import altair as alt
    import marimo as mo
    import numpy as np
    import pandas as pd

    return Path, alt, json, mo, np, pd, sqlite3


@app.cell
def _(Path, mo, pd, sqlite3):
    DB_PATH = Path(__file__).parent.parent / "dbs" / "open_ire.db"
    mo.stop(
        not DB_PATH.exists(),
        mo.callout(
            mo.md(f"**Database not found:** `{DB_PATH}`"),
            kind="danger",
        ),
    )

    def query_df(sql: str, params: list | None = None) -> pd.DataFrame:
        con = sqlite3.connect(DB_PATH)
        try:
            return pd.read_sql_query(sql, con, params=params)
        finally:
            con.close()

    return (query_df,)


@app.cell
def _(mo):
    mo.md("""
    # OpenIRE Metadata Analysis

    Aggregations, keyword patterns, and topic models derived from abstracts
    of articles collected across open-access research repositories.
    """)
    return


@app.cell
def _(pd, query_df):
    articles_df = query_df(
        """
        SELECT id, title, authors, abstract, doi, eissn, isbn, issn,
               publication_date, reference, repository, url,
               created_at, updated_at, extra
        FROM article
        """
    )
    articles_df["publication_date"] = pd.to_datetime(
        articles_df["publication_date"], errors="coerce"
    )

    files_df = query_df(
        """
        SELECT id, article_id, extension, size, url, created_at
        FROM articlefile
        """
    )
    return articles_df, files_df


@app.cell
def _(articles_df, json, np):
    _extras = [json.loads(x) if isinstance(x, str) and x else {} for x in articles_df["extra"]]

    articles = articles_df.assign(
        extra_keywords=[d.get("keywords", []) for d in _extras],
        extra_publisher=[d.get("publisher") for d in _extras],
        extra_language=[d.get("language") for d in _extras],
        has_abstract=articles_df["abstract"].notna() & (articles_df["abstract"] != ""),
        has_doi=articles_df["doi"].notna() & (articles_df["doi"] != ""),
        has_authors=articles_df["authors"].notna() & (articles_df["authors"] != ""),
        has_issn=articles_df["issn"].notna() & (articles_df["issn"] != ""),
        has_keywords=[len(d.get("keywords", [])) > 0 for d in _extras],
        pub_year=articles_df["publication_date"]
        .dt.year.where(articles_df["publication_date"].dt.year.between(1950, 2030), other=np.nan)
        .astype("Int64"),
    )
    return (articles,)


@app.cell
def _(mo):
    mo.md("""
    ## Collection overview
    """)
    return


@app.cell
def _(articles, files_df, mo):
    _n_articles = len(articles)
    _n_files = len(files_df)
    _total_gb = files_df["size"].sum() / 1_073_741_824
    _pct_abstract = articles["has_abstract"].mean() * 100 if _n_articles > 0 else 0
    _pct_doi = articles["has_doi"].mean() * 100 if _n_articles > 0 else 0

    mo.hstack(
        [
            mo.stat(value=f"{_n_articles:,}", label="Articles"),
            mo.stat(value=f"{_n_files:,}", label="Files"),
            mo.stat(value=f"{_total_gb:,.1f} GB", label="Total file size"),
            mo.stat(value=f"{_pct_abstract:.0f}%", label="Abstract coverage"),
            mo.stat(value=f"{_pct_doi:.0f}%", label="DOI coverage"),
        ],
        justify="start",
        gap=1,
    )
    return


@app.cell
def _(alt, articles):
    _repo_counts = articles.groupby("repository").size().reset_index(name="count")
    (
        alt.Chart(_repo_counts)
        .mark_bar()
        .encode(
            x=alt.X("repository:N", sort="-y", title="Repository"),
            y=alt.Y("count:Q", title="Article count"),
            color=alt.Color("repository:N", legend=None),
            tooltip=["repository", "count"],
        )
        .properties(title="Articles by repository", width=600)
    )
    return


@app.cell
def _(alt, articles):
    _year_repo = (
        articles.dropna(subset=["pub_year"])
        .groupby(["pub_year", "repository"])
        .size()
        .reset_index(name="count")
    )
    (
        alt.Chart(_year_repo)
        .mark_line(point=True)
        .encode(
            x=alt.X("pub_year:Q", title="Publication year", axis=alt.Axis(format="d")),
            y=alt.Y("count:Q", title="Articles"),
            color="repository:N",
            tooltip=["pub_year", "repository", "count"],
        )
        .properties(title="Articles over time by repository", width=600)
    )
    return


@app.cell
def _(alt, articles):
    _doi_by_year = (
        articles.dropna(subset=["pub_year"])
        .groupby(["pub_year", "repository"])["has_doi"]
        .mean()
        .reset_index(name="doi_rate")
    )
    (
        alt.Chart(_doi_by_year)
        .mark_line(point=True)
        .encode(
            x=alt.X("pub_year:Q", title="Publication year", axis=alt.Axis(format="d")),
            y=alt.Y("doi_rate:Q", title="DOI coverage", axis=alt.Axis(format=".0%")),
            color="repository:N",
            tooltip=[
                "pub_year",
                "repository",
                alt.Tooltip("doi_rate:Q", format=".1%"),
            ],
        )
        .properties(title="DOI coverage over time by repository", width=600)
    )
    return


@app.cell
def _(alt, articles):
    _completeness_cols = [
        "has_abstract",
        "has_doi",
        "has_authors",
        "has_issn",
        "has_keywords",
    ]
    _completeness = articles.groupby("repository")[_completeness_cols].mean().reset_index()
    _long = _completeness.melt(id_vars="repository", var_name="field", value_name="completeness")
    _long["field"] = _long["field"].str.replace("has_", "", regex=False)

    (
        alt.Chart(_long)
        .mark_bar()
        .encode(
            x=alt.X("completeness:Q", title="Fill rate", axis=alt.Axis(format=".0%")),
            y=alt.Y("field:N", title="Field"),
            color="repository:N",
            yOffset="repository:N",
            tooltip=[
                "repository",
                "field",
                alt.Tooltip("completeness:Q", format=".1%"),
            ],
        )
        .properties(title="Field completeness by repository", width=600)
    )
    return


@app.cell
def _(mo):
    mo.md("""
    ## Article Keywords
    """)
    return


@app.cell
def _(alt, articles, mo):
    _kw_df = articles.explode("extra_keywords").dropna(subset=["extra_keywords"])
    if len(_kw_df) == 0:
        mo.output.replace(
            mo.callout(
                mo.md("No keyword metadata available in the collected data."),
                kind="info",
            )
        )
    else:
        _kw_counts = _kw_df["extra_keywords"].value_counts().head(30).reset_index()
        _kw_counts.columns = ["keyword", "count"]
        _chart = (
            alt.Chart(_kw_counts)
            .mark_bar()
            .encode(
                x=alt.X("count:Q", title="Articles"),
                y=alt.Y("keyword:N", sort="-x", title="Keyword"),
                tooltip=["keyword", "count"],
            )
            .properties(title="Top 30 keywords (repository metadata)", width=600)
        )
        mo.output.replace(_chart)
    return


@app.cell
def _(alt, articles, mo):
    _pub_df = articles.dropna(subset=["extra_publisher"])
    if len(_pub_df) == 0:
        mo.output.replace(
            mo.callout(
                mo.md("No publisher metadata available in the collected data."),
                kind="info",
            )
        )
    else:
        _pub_counts = _pub_df["extra_publisher"].value_counts().head(15).reset_index()
        _pub_counts.columns = ["publisher", "count"]
        _chart = (
            alt.Chart(_pub_counts)
            .mark_bar()
            .encode(
                x=alt.X("count:Q", title="Articles"),
                y=alt.Y("publisher:N", sort="-x", title="Publisher"),
                tooltip=["publisher", "count"],
            )
            .properties(title="Top 15 publishers", width=600)
        )
        mo.output.replace(_chart)
    return


@app.cell
def _(mo):
    mo.md("""
    ## Topic Modeling
    """)
    return


@app.cell
def _():
    from sklearn.decomposition import NMF
    from sklearn.feature_extraction.text import TfidfVectorizer

    return NMF, TfidfVectorizer


@app.cell
def _(mo):
    n_topics_slider = mo.ui.slider(start=3, stop=20, value=8, label="Number of topics")
    mo.output.replace(n_topics_slider)
    return (n_topics_slider,)


@app.cell
def _(TfidfVectorizer, articles):
    _corpus_df = articles.dropna(subset=["abstract"])
    _corpus_df = _corpus_df[_corpus_df["abstract"].str.strip() != ""]
    all_abstracts = _corpus_df[["id", "title", "repository", "publication_date", "abstract"]]

    tfidf_vectorizer = TfidfVectorizer(
        max_features=2000,
        stop_words="english",
        min_df=5,
        max_df=0.7,
        ngram_range=(1, 2),
    )
    tfidf_matrix = tfidf_vectorizer.fit_transform(all_abstracts["abstract"])
    return tfidf_matrix, tfidf_vectorizer


@app.cell
def _(alt, np, pd, tfidf_matrix, tfidf_vectorizer):
    _mean_tfidf = np.asarray(tfidf_matrix.mean(axis=0)).flatten()
    _feature_names = tfidf_vectorizer.get_feature_names_out()
    _top_idx = _mean_tfidf.argsort()[-30:][::-1]
    _word_df = pd.DataFrame({"term": _feature_names[_top_idx], "mean_tfidf": _mean_tfidf[_top_idx]})
    (
        alt.Chart(_word_df)
        .mark_bar()
        .encode(
            x=alt.X("mean_tfidf:Q", title="Mean TF-IDF score"),
            y=alt.Y("term:N", sort="-x", title="Term"),
            tooltip=["term", alt.Tooltip("mean_tfidf:Q", format=".4f")],
        )
        .properties(title="Top 30 terms by mean TF-IDF", width=600)
    )
    return


@app.cell
def _(NMF, mo, n_topics_slider, tfidf_matrix):
    _model = NMF(n_components=n_topics_slider.value, random_state=42, max_iter=300)

    with mo.status.spinner("Fitting topic model..."):
        all_doc_topics = _model.fit_transform(tfidf_matrix)

    topic_model = _model
    return all_doc_topics, topic_model


@app.cell
def _(mo, pd, tfidf_vectorizer, topic_model):
    _feature_names = tfidf_vectorizer.get_feature_names_out()
    _topics = []
    for _idx, _weights in enumerate(topic_model.components_):
        _top_word_idx = _weights.argsort()[-10:][::-1]
        _top_words = [_feature_names[i] for i in _top_word_idx]
        _topics.append({"Topic": f"Topic {_idx + 1}", "Top keywords": ", ".join(_top_words)})
    _topic_df = pd.DataFrame(_topics)
    mo.ui.table(_topic_df, selection=None)
    return


@app.cell
def _(all_doc_topics, alt, np, pd):
    _dominant = np.argmax(all_doc_topics, axis=1)
    _dist = pd.Series(_dominant).value_counts().sort_index().reset_index()
    _dist.columns = ["topic_num", "count"]
    _dist["topic"] = _dist["topic_num"].apply(lambda x: f"Topic {x + 1}")

    (
        alt.Chart(_dist)
        .mark_bar()
        .encode(
            x=alt.X("topic:N", sort=None, title="Topic"),
            y=alt.Y("count:Q", title="Number of articles"),
            color=alt.Color("topic:N", legend=None),
            tooltip=["topic", "count"],
        )
        .properties(title="Article distribution across topics", width=600)
    )
    return


if __name__ == "__main__":
    app.run()
