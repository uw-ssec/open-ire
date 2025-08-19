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

Copy the example environment file and configure your settings. This step is
necessary for storing collected files in a Microsoft SharePoint Drive.
Alternatively, you can disable the `SharePointPipeline` in
`src/open_ire/settings.py`.

```bash
pixi run dotenv
```

Edit the `.env` file with your SharePoint credentials:

```bash
SHAREPOINT_TENANT_ID=<your_application_tenant_id>
SHAREPOINT_CLIENT_ID=<your_application_id>
SHAREPOINT_SITE_ID=<your_sharepoint_site_id>
SHAREPOINT_CLIENT_SECRET=<your_application_client_secret>
```

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

For detailed development setup including pre-commit hooks, please see
[CONTRIBUTING.md](CONTRIBUTING.md).

## Running

This project includes several spiders for crawling open-access repositories. Run
a spider with:

```bash
pixi run spider <spider_name>
```

Each spider supports a custom list of terms to include in the search:

```bash
pixi run spider <spider_name> --terms "term1,term2,..."
```

## Contributing

We welcome contributions! Please see our contribution guidelines:

- [CONTRIBUTING.md](CONTRIBUTING.md)
- [RSE Guidelines](https://rse-guidelines.readthedocs.io/en/latest/)
- [Scientific Python Development Guide](https://learn.scientific-python.org/development/)
