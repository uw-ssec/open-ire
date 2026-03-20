<div align="center">

# Open IRE

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: BSD-3-Clause](https://img.shields.io/badge/License-BSD%203--Clause-blue.svg)](https://opensource.org/licenses/BSD-3-Clause)
[![Pixi](https://img.shields.io/badge/pixi-package%20manager-4051b5.svg)](https://pixi.sh/)
[![Hatch project](https://img.shields.io/badge/%F0%9F%A5%9A-Hatch-4051b5.svg)](https://github.com/pypa/hatch)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&logoColor=white)](https://github.com/pre-commit/pre-commit)

</div>

<div align="center">
Open Institutional Repository Expansion (IRE) is a configurable crawler for collecting articles from open-access research repositories.
</div>

## Installation

### 1. Prerequisites

First, install the [Pixi](https://pixi.sh/latest/#getting-started) package
manager:

**macOS/Linux:**

```bash
curl -fsSL https://pixi.sh/install.sh | sh
```

**Windows:**

```powershell
powershell -ExecutionPolicy ByPass -c "irm -useb https://pixi.sh/install.ps1 | iex"
```

For more installation options, visit the
[Pixi installation guide](https://pixi.sh/latest/#installation).

### 2. Installation from Source

```bash
git clone https://github.com/uw-ssec/open-ire.git
cd open-ire
pixi install
```

## Getting Started

### 1. Environment Setup

```bash
pixi run dotenv
```

This command creates the environment file template `.env` that you then need to
edit to configure your settings. The only required setting is `ENVIRONMENT`,
which needs to be set to either `development` or `production`.

To store collected files in a Microsoft SharePoint Drive, you also need to set
your SharePoint credentials:

```bash
SHAREPOINT_TENANT_ID=<your_application_tenant_id>
SHAREPOINT_CLIENT_ID=<your_application_id>
SHAREPOINT_SITE_ID=<your_sharepoint_site_id>
SHAREPOINT_CLIENT_SECRET=<your_application_client_secret>
```

Alternatively, you can disable the `SharePointPipeline` in
`src/open_ire/settings.py`.

### 2. Activate the Environment

Activate the Pixi environment:

```bash
pixi shell
```

Or run commands directly with:

```bash
pixi run <command>
```

### 3. Development Setup

For detailed development setup, including pre-commit hooks, please see
[CONTRIBUTING.md](CONTRIBUTING.md).

## Running

This project includes spiders for crawling repositories using two main methods:
a list of keywords or a CSV file of author names.

### Crawl and Resume

To run a spider with a persistent crawl state (Scrapy `JOBDIR`) and optionally
skip already-known files, use the `resume` command:

```bash
pixi run resume <spider_name> [--skip-existing]
```

For example:

```bash
pixi run resume eric --skip-existing
```

### Tracking Deleted Articles

To detect previously collected article metadata and downloaded files that are no
longer available, run the `unavailable_articles` spider. It reads from
`OPEN_IRE_DATABASE_FILE` and writes a CSV report under `output/`.

```bash
pixi run resume unavailable_articles
```

### Search by Keyword

To run a spider with a custom list of search terms, use the `search-terms`
command:

```bash
pixi run search-terms <spider_name> "term1,term2,..." [<page>]
```

For example, to search the `eric` repository:

```bash
pixi run search-terms eric "ocean acidification,coral bleaching"
```

### Search by Author

To search for a single author's name, use the `search-author` (singular)
command:

```bash
pixi run search-author <spider_name> "<author's full name>"
```

For example:

```bash
pixi run search-author openalex "Michelle Habell-Pallán"
```

To run a spider that supports searching by author against a list of authors, use
the `search-authors` (plural) command. This requires a CSV file with
`FirstName`, `MiddleNames` (optional), `LastName`, and `Email` columns.

```bash
pixi run search-authors <spider_name> "<path_to_csv>"
```

For example, to search `openalex` for publications by any one among a number of
authors:

```bash
pixi run search-authors openalex "data/authors.csv"
```

### Notebooks

This project includes [marimo](https://marimo.io/) notebooks for data analysis
under `notebooks/`.

| Notebook                  | Description                                                                      |
| ------------------------- | -------------------------------------------------------------------------------- |
| `metadata_analysis.py`    | Collection stats, repository breakdowns, and text analysis                       |
| `unavailable_articles.py` | Re-checks URLs from an unavailable-articles CSV to identify which have recovered |

To open a notebook in the interactive editor:

```bash
pixi run -e dev marimo edit notebooks/metadata_analysis.py
```

To run a notebook as a read-only app:

```bash
pixi run -e dev marimo run notebooks/metadata_analysis.py
```

## Contributing

We welcome contributions! Please see our contribution guidelines:

- [CONTRIBUTING.md](CONTRIBUTING.md)
- [RSE Guidelines](https://rse-guidelines.readthedocs.io/en/latest/)
- [Scientific Python Development Guide](https://learn.scientific-python.org/development/)
