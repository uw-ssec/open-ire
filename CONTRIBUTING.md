# Contributing to Open IRE

Thank you for your interest in contributing to **Open IRE**! This guide provides
step-by-step instructions to set up the project locally. Follow these guidelines
to get started.

Read our
[Code of Conduct](https://github.com/uw-ssec/code-of-conduct/blob/main/CODE_OF_CONDUCT.md)
to keep our community approachable and respectable.

## Pull Requests

We welcome contributions! Please follow these guidelines when submitting a Pull
Request:

- It may be helpful to review
  [this tutorial](https://www.dataschool.io/how-to-contribute-on-github/) on how
  to contribute to open source projects. A typical task workflow is:

  - [Fork](https://docs.github.com/en/get-started/quickstart/fork-a-repo) the
    code repository specified in the task and
    [clone](https://docs.github.com/en/repositories/creating-and-managing-repositories/cloning-a-repository)
    it locally.
  - Review the repo's README.md and CONTRIBUTING.md files to understand what is
    required to run and modify this code.
  - Create a branch in your local repo to implement the task.
  - Commit your changes to the branch and push it to the remote repo.
  - Create a pull request, adding the task owner as the reviewer.

- Please follow the
  [Conventional Commits](https://github.com/uw-ssec/rse-guidelines/blob/main/conventional-commits.md)
  naming for pull request titles.

Your contributions make this project better—thank you for your support! 🚀

## Development

### General Workflow

1. Set up your development environment with `pixi install`.
2. Configure your `.env` file.
3. Install pre-commit hooks with `pixi run pre-commit-install`.
4. Create a feature branch.
5. Make your changes and ensure tests and pre-commit checks pass.
6. Submit a pull request.

> [!NOTE]
>
> Feature requests and bug reports are tracked via
> [GitHub Issues](https://github.com/uw-ssec/open-ire/issues). Please check for
> existing issues before starting work, and open a new one if needed.

### Configuring Pre-commit

PRs will fail style and formatting checks as configured by
[pre-commit](https://pre-commit.com/), but you can set up your local repository
such that precommit runs every time you commit. This way, you can fix any errors
before you send out pull requests!!

To do this, install [Pixi](https://pixi.sh/latest/) using either the
[instructions on their website](https://pixi.sh/latest/#installation), or the
commands below:

**macOS/Linux:**

```bash
curl -fsSL https://pixi.sh/install.sh | sh
```

**Windows:**

```powershell
powershell -ExecutionPolicy ByPass -c "irm -useb https://pixi.sh/install.ps1 | iex"
```

#### Configure Pre-commit to run on every commit

Then, once Pixi is installed, run the following command to set up pre-commit
checks on every commit

```
pixi run pre-commit-install
```

#### Manually run pre-commit on non-committed files

```
pixi run pre-commit
```

#### Manually run pre-commit on all files

```
pixi run pre-commit-all
```

#### Available Pixi Environments

- `default`: Basic execution environment.
- `dev`: Development environment with testing tools.

To activate one of these environments, run the following command:

```bash
pixi shell -e <environment>
```

### Database Migrations

Schema changes are managed with [Alembic](https://alembic.sqlalchemy.org/).
Migrations run automatically when spiders start, but when you modify the
database models in `src/open_ire/models.py`, you must generate a corresponding
migration:

```bash
pixi run -e dev alembic revision --autogenerate -m "brief description"
```

This creates a new migration file
`src/open_ire/migrations/versions/<hash>_brief_description.py`. Review the
generated file to ensure it accurately captures your changes, then commit it
alongside the model changes. See the
[Alembic documentation](https://alembic.sqlalchemy.org/en/latest/tutorial.html#create-a-migration-script)
for more information.

Other useful migration commands:

```bash
pixi run -e dev alembic upgrade head          # Apply pending migrations manually
pixi run -e dev alembic history --verbose     # Show migration history
```

### Running Tests

This project uses [pytest](https://pytest.org/) as its testing framework. You
can execute the following command to run the full test suite:

```bash
pixi run test
```

This command executes `python -m pytest -ra --cov=open_ire`, which:

- Runs all tests in the `tests/` directory.
- Shows a short test summary (`-ra`).
- Generates coverage reports for the `src/open_ire` package.

#### Running Specific Tests

Run all tests in a specific file:

```bash
python -m pytest tests/test_sharepoint.py
```

Run a specific test function:

```bash
python -m pytest tests/test_sharepoint.py::TestSharePoint::test_init_with_env
```

Make sure to activate the `dev` environment before running tests.

### Project Architecture

See the [Architecture](docs/architecture.md) documentation for an overview of
the different system components.
