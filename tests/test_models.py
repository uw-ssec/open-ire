"""Tests for models.py module."""

import re

import pytest
from sqlmodel import SQLModel

from open_ire import models


def table_models() -> list[type[SQLModel]]:
    """Collect every SQLModel in models.py that maps to a table."""
    found = []
    for obj in vars(models).values():
        if not isinstance(obj, type) or not issubclass(obj, SQLModel):
            continue
        if getattr(obj, "__table__", None) is None:
            continue
        found.append(obj)
    return sorted(set(found), key=lambda cls: cls.__name__)


def to_snake_case(name: str) -> str:
    """Convert a CamelCase name to snake_case, keeping acronyms intact."""
    name = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).lower()


class TestTableNames:
    """Tests for the table names declared by the SQLModel classes."""

    def test_table_models_are_discovered(self) -> None:
        """Guard against the discovery helper silently finding nothing."""
        assert models.Article in table_models()

    @pytest.mark.parametrize("cls", table_models(), ids=lambda cls: cls.__name__)
    def test_table_names_are_snake_case(self, cls: type[SQLModel]) -> None:
        """Ensure that the table names are snake_case."""
        assert cls.__tablename__ == to_snake_case(cls.__name__)
