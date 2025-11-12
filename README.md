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
A configurable crawler for collecting articles from open-access research repositories.
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
a list of keywords or a CSV file of faculty names.

### Search by Keyword

To run a spider with a custom list of search terms, use the `terms-search`
command:

```bash
pixi run terms-search <spider_name> "term1,term2,..." [<page>]
```

For example, to search the `eric` repository:

```bash
pixi run terms-search eric "ocean acidification,coral bleaching"
```

### Search by Faculty

To run a spider that supports searching by author against a list of faculty, use
the `faculty-search` command. This requires a CSV file with `FirstName`,
`LastName`, and `Email` columns.

```bash
pixi run faculty-search <spider_name> <path_to_csv>
```

For example, to search `openalex` using a faculty file:

```bash
pixi run faculty-search openalex data/faculty.csv
```

## Contributing

We welcome contributions! Please see our contribution guidelines:

- [CONTRIBUTING.md](CONTRIBUTING.md)
- [RSE Guidelines](https://rse-guidelines.readthedocs.io/en/latest/)
- [Scientific Python Development Guide](https://learn.scientific-python.org/development/)
