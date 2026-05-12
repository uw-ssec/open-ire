"""Tests for DatamartSpider."""

from collections.abc import Generator
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from open_ire.items import AuthorItem
from open_ire.spiders.datamart import DatamartSpider


@pytest.fixture
def spider() -> Generator[DatamartSpider, None, None]:
    with (
        patch.object(DatamartSpider, "logger", new_callable=MagicMock),
        patch.object(DatamartSpider, "_create_datamart_engine", return_value=MagicMock()),
    ):
        yield DatamartSpider()


def _make_row(
    first: str | None,
    last: str | None,
    netid: str | None,
    orcid_id: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        display_first_name=first,
        display_last_name=last,
        uw_netid=netid,
        orcid_id=orcid_id,
    )


def _mock_engine(rows: list[SimpleNamespace]) -> MagicMock:
    mock_engine = MagicMock()
    mock_conn = mock_engine.connect.return_value.__enter__.return_value
    mock_conn.execute.return_value = rows
    return mock_engine


class TestCreateDatamartEngine:
    def test_raises_when_credentials_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for var in ("DATAMART_USER", "DATAMART_PASS", "DATAMART_HOST", "DATAMART_DB"):
            monkeypatch.delenv(var, raising=False)

        with pytest.raises(RuntimeError):
            DatamartSpider._create_datamart_engine()

    def test_creates_engine_with_credentials(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATAMART_USER", "user")
        monkeypatch.setenv("DATAMART_PASS", "pass")
        monkeypatch.setenv("DATAMART_HOST", "host")
        monkeypatch.setenv("DATAMART_PORT", "5433")
        monkeypatch.setenv("DATAMART_DB", "db")

        with patch("open_ire.spiders.datamart.sa_create_engine") as mock_create:
            result = DatamartSpider._create_datamart_engine()

        mock_create.assert_called_once_with(
            "postgresql+psycopg2://user:pass@host:5433/db",
            connect_args={"sslmode": "require"},
            pool_pre_ping=True,
        )
        assert result is mock_create.return_value

    def test_disposes_engine_when_set(self, spider: DatamartSpider) -> None:
        mock_engine = MagicMock()
        spider.datamart_engine = mock_engine

        spider.closed("finished")

        mock_engine.dispose.assert_called_once()


class TestLoadFaculty:
    def test_returns_record_for_valid_row(self, spider: DatamartSpider) -> None:
        spider.datamart_engine = _mock_engine([_make_row("Jane", "Smith", "jsmith")])

        records = spider._load_faculty()

        assert records == [
            {"first_name": "Jane", "last_name": "Smith", "uw_netid": "jsmith", "orcid_id": ""}
        ]

    def test_strips_whitespace_from_fields(self, spider: DatamartSpider) -> None:
        spider.datamart_engine = _mock_engine([_make_row("  Jane  ", "  Smith  ", "  jsmith  ")])

        records = spider._load_faculty()

        assert records == [
            {"first_name": "Jane", "last_name": "Smith", "uw_netid": "jsmith", "orcid_id": ""}
        ]

    def test_stores_orcid_when_present(self, spider: DatamartSpider) -> None:
        spider.datamart_engine = _mock_engine(
            [
                _make_row("Jane", "Smith", "jsmith", orcid_id="0000-0002-1234-5678"),
            ]
        )

        records = spider._load_faculty()

        assert records[0]["orcid_id"] == "0000-0002-1234-5678"

    def test_skips_row_with_whitespace_only_first_name(self, spider: DatamartSpider) -> None:
        spider.datamart_engine = _mock_engine([_make_row("   ", "Smith", "jsmith")])

        assert spider._load_faculty() == []

    def test_skips_row_with_null_last_name(self, spider: DatamartSpider) -> None:
        spider.datamart_engine = _mock_engine([_make_row("Jane", None, "jsmith")])

        assert spider._load_faculty() == []

    def test_skips_row_with_null_netid(self, spider: DatamartSpider) -> None:
        spider.datamart_engine = _mock_engine([_make_row("Jane", "Smith", None)])

        assert spider._load_faculty() == []

    def test_skips_invalid_rows_among_valid_ones(self, spider: DatamartSpider) -> None:
        spider.datamart_engine = _mock_engine(
            [
                _make_row("Jane", "Smith", "jsmith"),
                _make_row(None, "Jones", "bjones"),
                _make_row("Robert", "Lee", "rlee"),
            ]
        )

        records = spider._load_faculty()

        assert len(records) == 2
        assert records[0]["uw_netid"] == "jsmith"
        assert records[1]["uw_netid"] == "rlee"


def _faculty_row(
    first: str = "Jane",
    last: str = "Smith",
    netid: str = "jsmith",
    orcid_id: str = "",
) -> dict[str, str]:
    return {"first_name": first, "last_name": last, "uw_netid": netid, "orcid_id": orcid_id}


class TestStart:
    @pytest.mark.asyncio
    async def test_yields_one_item_per_faculty_row(self, spider: DatamartSpider) -> None:
        spider._load_faculty = MagicMock(  # type: ignore[method-assign]
            return_value=[
                _faculty_row(netid="jsmith"),
                _faculty_row(first="Robert", last="Lee", netid="rlee"),
            ]
        )

        items = [item async for item in spider.start()]

        assert len(items) == 2
        assert all(isinstance(item, AuthorItem) for item in items)

    @pytest.mark.asyncio
    async def test_author_item_has_correct_parsed_name(self, spider: DatamartSpider) -> None:
        spider._load_faculty = MagicMock(  # type: ignore[method-assign]
            return_value=[
                _faculty_row(first="Jane", last="Smith", netid="jsmith"),
            ]
        )

        items = [item async for item in spider.start()]

        assert items[0].author.first_name == "Jane"
        assert items[0].author.last_name == "Smith"

    @pytest.mark.asyncio
    async def test_author_item_carries_uw_netid_identifier(self, spider: DatamartSpider) -> None:
        spider._load_faculty = MagicMock(  # type: ignore[method-assign]
            return_value=[
                _faculty_row(netid="jsmith"),
            ]
        )

        items = [item async for item in spider.start()]

        assert {"authority": "uw_netid", "identifier": "jsmith"} in items[0].identifiers

    @pytest.mark.asyncio
    async def test_author_item_carries_orcid_when_present(self, spider: DatamartSpider) -> None:
        spider._load_faculty = MagicMock(  # type: ignore[method-assign]
            return_value=[
                _faculty_row(netid="jsmith", orcid_id="0000-0002-1234-5678"),
            ]
        )

        items = [item async for item in spider.start()]

        assert {"authority": "orcid", "identifier": "0000-0002-1234-5678"} in items[0].identifiers

    @pytest.mark.asyncio
    async def test_author_item_omits_orcid_when_absent(self, spider: DatamartSpider) -> None:
        spider._load_faculty = MagicMock(  # type: ignore[method-assign]
            return_value=[
                _faculty_row(netid="jsmith", orcid_id=""),
            ]
        )

        items = [item async for item in spider.start()]

        authorities = {ident["authority"] for ident in items[0].identifiers}
        assert "orcid" not in authorities

    @pytest.mark.asyncio
    async def test_yields_nothing_when_no_faculty_loaded(self, spider: DatamartSpider) -> None:
        spider._load_faculty = MagicMock(return_value=[])  # type: ignore[method-assign]

        items = [item async for item in spider.start()]

        assert items == []
